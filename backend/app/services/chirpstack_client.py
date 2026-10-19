from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import httpx
from app.config import settings
from app.schemas import CommState

logger = logging.getLogger("lorawan_monitor.chirpstack")


def evaluate_communication_state(
    last_seen_at: Optional[datetime],
    threshold_minutes: int,
    now: Optional[datetime] = None,
) -> Tuple[CommState, str, str, Optional[int]]:
    """
    Calcula el estado operativo real a partir de la última actividad conocida (lastSeenAt)
    y un umbral configurable, sin presentarlo como una confirmación directa si es inferido.
    """
    current_time = now or datetime.now(timezone.utc)
    if last_seen_at is None:
        return (
            "UNKNOWN",
            "Estado desconocido",
            "Sin registro previo de transmisión en ChirpStack (nunca visto)",
            None,
        )

    elapsed_seconds = max(0.0, (current_time - last_seen_at).total_seconds())
    elapsed_minutes = int(elapsed_seconds // 60)

    if elapsed_minutes <= threshold_minutes:
        return (
            "ACTIVE",
            "Activo (comunicación reciente)",
            f"Calculado por última actividad hace {elapsed_minutes} min (umbral ≤ {threshold_minutes} min)",
            elapsed_minutes,
        )
    return (
        "NO_COMMUNICATION",
        "Sin comunicación",
        f"Inferido por inactividad de {elapsed_minutes} min (supera umbral de {threshold_minutes} min)",
        elapsed_minutes,
    )


def format_battery(battery_level: Optional[float], external_power: Optional[bool] = None) -> str:
    if external_power is True:
        return "Alimentación externa"
    if battery_level is None or battery_level < 0 or battery_level > 100:
        return "No disponible"
    return f"{int(round(battery_level))}%"


def format_location(lat: Optional[float], lon: Optional[float]) -> Tuple[bool, str]:
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        return False, "No disponible"
    return True, f"{lat:.5f}, {lon:.5f}"


def sanitize_mqtt_event(topic: str, raw_payload: bytes) -> Optional[Dict[str, Any]]:
    """
    Procesa un mensaje MQTT de ChirpStack v4 descartando inmediatamente cualquier
    payload de aplicación ('data', 'object', 'rxInfo', 'txInfo') para garantizar
    que jamás ingresen mediciones de sensores al sistema.
    """
    try:
        parsed = json.loads(raw_payload.decode("utf-8"))
    except Exception:
        return None

    # Purga explícita de campos de sensores/payloads y radiofrecuencia
    for forbidden_key in ("data", "object", "rxInfo", "txInfo", "dr", "fCnt", "fPort"):
        parsed.pop(forbidden_key, None)

    device_info = parsed.get("deviceInfo") or {}
    dev_eui = device_info.get("devEui")
    event_time_str = parsed.get("time")

    event_time = datetime.now(timezone.utc)
    if isinstance(event_time_str, str):
        try:
            event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    if "/event/up" in topic and dev_eui:
        return {
            "kind": "NODE_ACTIVITY",
            "dev_eui": dev_eui,
            "name": device_info.get("deviceName"),
            "tenant_id": device_info.get("tenantId"),
            "application_id": device_info.get("applicationId"),
            "last_seen_at": event_time,
        }

    if "/event/status" in topic and dev_eui:
        battery_level = parsed.get("batteryLevel")
        external_power = parsed.get("externalPowerSource")
        return {
            "kind": "NODE_STATUS",
            "dev_eui": dev_eui,
            "last_seen_at": event_time,
            "battery_level": float(battery_level)
            if isinstance(battery_level, (int, float)) and not parsed.get("batteryLevelUnavailable", False)
            else None,
            "external_power": bool(external_power) if external_power is not None else None,
        }

    if "/gateway/" in topic and "gatewayId" in parsed:
        return {
            "kind": "GATEWAY_STATE",
            "gateway_id": parsed["gatewayId"],
            "last_seen_at": event_time,
        }

    return None


class ChirpStackV4Client:
    """
    Cliente oficial REST (gRPC-Gateway) para ChirpStack v4.
    Opera únicamente en el backend utilizando CHIRPSTACK_API_TOKEN.
    """

    @property
    def base_url(self) -> str:
        return settings.chirpstack_api_url.rstrip("/")

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Grpc-Metadata-Authorization": f"Bearer {settings.chirpstack_api_token}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            verify=settings.chirpstack_verify_ssl, timeout=12.0
        ) as client:
            response = await client.get(
                f"{self.base_url}{path}", headers=self.headers, params=params
            )
            response.raise_for_status()
            return response.json()

    async def fetch_tenants(self) -> List[Dict[str, Any]]:
        data = await self._get("/api/tenants", {"limit": 100})
        return [
            {"tenant_id": item["id"], "name": item.get("name", "Sin nombre")}
            for item in data.get("result", [])
        ]

    async def fetch_applications(self, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._get("/api/applications", {"tenantId": tenant_id, "limit": 100})
        return [
            {
                "application_id": item["id"],
                "tenant_id": tenant_id,
                "name": item.get("name", "Sin nombre"),
            }
            for item in data.get("result", [])
        ]

    async def fetch_devices(self, application_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._get("/api/devices", {"applicationId": application_id, "limit": 500})
        devices: List[Dict[str, Any]] = []
        for item in data.get("result", []):
            last_seen = self._parse_iso(item.get("lastSeenAt"))
            created_at = self._parse_iso(item.get("createdAt"))
            status_block = item.get("deviceStatus") or {}
            battery_raw = status_block.get("batteryLevel")
            ext_power = status_block.get("externalPowerSource")

            battery_level: Optional[float] = None
            if isinstance(battery_raw, (int, float)) and 0 <= battery_raw <= 100:
                battery_level = float(battery_raw)

            devices.append(
                {
                    "dev_eui": item["devEui"],
                    "name": item.get("name", item["devEui"]),
                    "application_id": application_id,
                    "tenant_id": tenant_id,
                    "last_seen_at": last_seen,
                    "registered_at": created_at,
                    "battery_level": battery_level,
                    "external_power": bool(ext_power) if ext_power is not None else None,
                    "latitude": None,
                    "longitude": None,
                }
            )
        return devices

    async def fetch_gateways(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": 200}
        if tenant_id:
            params["tenantId"] = tenant_id
        data = await self._get("/api/gateways", params)
        gateways: List[Dict[str, Any]] = []
        for item in data.get("result", []):
            loc = item.get("location") or {}
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            has_coords, _ = format_location(lat, lon)
            gateways.append(
                {
                    "gateway_id": item["gatewayId"],
                    "name": item.get("name", item["gatewayId"]),
                    "tenant_id": item.get("tenantId"),
                    "last_seen_at": self._parse_iso(item.get("lastSeenAt")),
                    "latitude": float(lat) if has_coords else None,
                    "longitude": float(lon) if has_coords else None,
                }
            )
        return gateways

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None