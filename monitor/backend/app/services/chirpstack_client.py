from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import base64
import json
import logging
import uuid
import httpx
from app.config import settings
from app.schemas import CommState

logger = logging.getLogger("lorawan_monitor.chirpstack")


def evaluate_communication_state(
    last_seen_at: Optional[datetime],
    threshold_value: int,
    now: Optional[datetime] = None,
    unit: str = "minutes",
) -> Tuple[CommState, str, str, Optional[int]]:
    """
    Calcula el estado operativo real a partir de la última actividad conocida (lastSeenAt)
    y un umbral configurable (en segundos o minutos). Por defecto evalúa en minutos para compatibilidad.
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
    elapsed_seconds_int = int(elapsed_seconds)
    elapsed_minutes = int(elapsed_seconds // 60)
    ts_str = last_seen_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    if unit == "seconds":
        threshold_sec = threshold_value
        threshold_label = f"{threshold_value}s"
        time_display = f"{elapsed_seconds_int}s"
        is_active = elapsed_seconds_int <= threshold_sec
    else:
        threshold_sec = threshold_value * 60
        threshold_label = f"{threshold_value} min"
        time_display = f"{elapsed_minutes} min"
        is_active = elapsed_minutes <= threshold_value

    if is_active:
        return (
            "ACTIVE",
            "Activo (comunicación reciente)",
            f"Calculado por última actividad hace {time_display} (umbral ≤ {threshold_label})",
            elapsed_minutes,
        )
    return (
        "NO_COMMUNICATION",
        "Sin comunicación",
        f"Inferido por inactividad de {time_display} (supera umbral de {threshold_label}). Última transmisión: {ts_str}",
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


def evaluate_signal_quality(rssi: Optional[int], snr: Optional[float]) -> Tuple[str, str, str]:
    """
    Evalúa la calidad de conexión RF del nodo a partir de RSSI (dBm) y SNR (dB).
    Retorna: (rssi_display, snr_display, signal_quality)
    """
    rssi_display = f"{int(rssi)} dBm" if rssi is not None else "— dBm"
    if snr is not None:
        sign = "+" if snr > 0 else ""
        snr_display = f"{sign}{snr:.1f} dB"
    else:
        snr_display = "— dB"

    if rssi is None and snr is None:
        return rssi_display, snr_display, "Sin datos"

    r_val = rssi if rssi is not None else -90
    s_val = snr if snr is not None else 2.0

    if r_val >= -85 and s_val >= 5.0:
        quality = "Excelente"
    elif r_val >= -100 and s_val >= 0.0:
        quality = "Buena"
    elif r_val >= -115 and s_val >= -7.5:
        quality = "Regular"
    else:
        quality = "Débil"

    return rssi_display, snr_display, quality


def parse_lorawan_phy_payload(phy_bytes: bytes) -> Dict[str, Any]:
    """
    Decodifica la cabecera física/MAC estándar LoRaWAN 1.0/1.1 desde bytes crudos (PHYPayload).
    Extrae MType, DevEUI/JoinEUI (en JoinRequest), DevAddr/FCnt/FPort/ADR (en DataUp/Down) y MIC.
    """
    if not phy_bytes:
        return {}
    mhdr = phy_bytes[0]
    mtype_num = (mhdr >> 5) & 0x07
    mtype_names = {
        0: "JoinRequest",
        1: "JoinAccept",
        2: "UnconfirmedDataUp",
        3: "UnconfirmedDataDown",
        4: "ConfirmedDataUp",
        5: "ConfirmedDataDown",
        6: "RejoinRequest",
        7: "Proprietary",
    }
    m_type = mtype_names.get(mtype_num, "Unknown")
    result: Dict[str, Any] = {
        "m_type": m_type,
        "phy_payload_hex": phy_bytes.hex(),
        "mic_hex": phy_bytes[-4:].hex() if len(phy_bytes) >= 5 else None,
    }

    if mtype_num == 0 and len(phy_bytes) >= 23:
        # JoinRequest: MHDR(1) | JoinEUI(8 LE) | DevEUI(8 LE) | DevNonce(2 LE) | MIC(4)
        join_eui = phy_bytes[1:9][::-1].hex()
        dev_eui = phy_bytes[9:17][::-1].hex()
        dev_nonce = int.from_bytes(phy_bytes[17:19], "little")
        result.update(
            {
                "join_eui": join_eui,
                "dev_eui": dev_eui,
                "dev_nonce": dev_nonce,
            }
        )
    elif mtype_num in (2, 3, 4, 5) and len(phy_bytes) >= 12:
        # DataUp / DataDown: MHDR(1) | DevAddr(4 LE) | FCtrl(1) | FCnt(2 LE) | FOpts(0..15) | [FPort(1) | FRMPayload] | MIC(4)
        dev_addr = phy_bytes[1:5][::-1].hex()
        f_ctrl = phy_bytes[5]
        adr = bool(f_ctrl & 0x80)
        ack = bool(f_ctrl & 0x20)
        f_opts_len = f_ctrl & 0x0F
        f_cnt = int.from_bytes(phy_bytes[6:8], "little")
        f_port: Optional[int] = None
        frm_hex: Optional[str] = None
        if len(phy_bytes) > 8 + f_opts_len + 4:
            f_port = phy_bytes[8 + f_opts_len]
            frm_hex = phy_bytes[9 + f_opts_len : -4].hex()
        result.update(
            {
                "dev_addr": dev_addr,
                "adr": adr,
                "ack": ack,
                "f_cnt": f_cnt,
                "f_port": f_port,
                "data_hex": frm_hex,
                "confirmed": mtype_num in (4, 5),
            }
        )
    return result


def parse_chirpstack_mqtt_event(topic: str, raw_payload: bytes) -> Optional[Dict[str, Any]]:
    """
    Procesa mensajes MQTT de ChirpStack v4:
    1) application/{app_id}/device/{dev_eui}/event/{up|join|status|ack|txack|log|location}
    2) application/{app_id}/device/{dev_eui}/command/down
    3) */gateway/{gateway_id}/event/{up|stats|ack} y */gateway/{gateway_id}/command/down
    Extrae métricas RF (RSSI en dBm, SNR en dB, SF, BW, Frecuencia, FCnt, FPort)
    y genera registros estructurados para Events y LoRaWAN Frames.
    """
    now = datetime.now(timezone.utc)
    parsed: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(raw_payload.decode("utf-8"))
    except Exception:
        parsed = None

    # Caso A: Eventos de aplicación/dispositivo en JSON (application/+/device/+/event/+)
    if parsed and ("/device/" in topic and ("/event/" in topic or "/command/" in topic)):
        parts = topic.split("/")
        event_type = parts[-1].lower() if parts else "up"
        device_info = parsed.get("deviceInfo") or {}
        dev_eui = (device_info.get("devEui") or parsed.get("devEui") or "").strip().lower()
        if not dev_eui and "device" in parts:
            idx = parts.index("device")
            if idx + 1 < len(parts):
                dev_eui = parts[idx + 1].strip().lower()
        if not dev_eui:
            return None

        event_time = now
        event_time_str = parsed.get("time")
        if isinstance(event_time_str, str):
            try:
                event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        device_name = device_info.get("deviceName") or dev_eui
        app_id = str(device_info.get("applicationId") or "")
        app_name = str(device_info.get("applicationName") or "")
        tenant_id = str(device_info.get("tenantId") or "")

        # Extraer rxInfo (RSSI en dBm y SNR en dB por gateway)
        rx_info_raw = parsed.get("rxInfo") or []
        rx_gateways: List[Dict[str, Any]] = []
        best_rssi: Optional[int] = None
        best_snr: Optional[float] = None
        best_gw_id: Optional[str] = None
        best_channel: Optional[int] = None

        for rx in rx_info_raw:
            if not isinstance(rx, dict):
                continue
            gw_id = str(rx.get("gatewayId") or "").strip().lower()
            rssi_val = int(round(float(rx["rssi"]))) if isinstance(rx.get("rssi"), (int, float)) else None
            snr_val = round(float(rx["snr"]), 1) if isinstance(rx.get("snr"), (int, float)) else None
            ch_val = int(rx["channel"]) if isinstance(rx.get("channel"), (int, float)) else None
            rx_gateways.append(
                {
                    "gateway_id": gw_id,
                    "rssi": rssi_val,
                    "snr": snr_val,
                    "channel": ch_val,
                    "rf_chain": rx.get("rfChain"),
                    "crc_status": rx.get("crcStatus", "CRC_OK"),
                }
            )
            if rssi_val is not None and (best_rssi is None or rssi_val > best_rssi):
                best_rssi = rssi_val
                best_gw_id = gw_id or best_gw_id
                best_channel = ch_val if ch_val is not None else best_channel
            if snr_val is not None and (best_snr is None or snr_val > best_snr):
                best_snr = snr_val
                if not best_gw_id and gw_id:
                    best_gw_id = gw_id

        # Extraer txInfo (Frecuencia MHz, Spreading Factor, Bandwidth kHz, Code Rate)
        tx_info_raw = parsed.get("txInfo") or {}
        freq_hz = tx_info_raw.get("frequency")
        freq_mhz: Optional[float] = (
            round(float(freq_hz) / 1_000_000.0, 3)
            if isinstance(freq_hz, (int, float)) and freq_hz > 0
            else None
        )
        lora_mod = ((tx_info_raw.get("modulation") or {}).get("lora")) or {}
        sf_val: Optional[int] = (
            int(lora_mod["spreadingFactor"])
            if isinstance(lora_mod.get("spreadingFactor"), (int, float))
            else None
        )
        bw_hz = lora_mod.get("bandwidth")
        bw_khz: Optional[int] = (
            int(float(bw_hz) // 1000) if isinstance(bw_hz, (int, float)) and bw_hz > 0 else None
        )
        code_rate: Optional[str] = lora_mod.get("codeRate")

        dr_val = int(parsed["dr"]) if isinstance(parsed.get("dr"), (int, float)) else None
        adr_val = bool(parsed["adr"]) if parsed.get("adr") is not None else None
        fcnt_val = int(parsed["fCnt"]) if isinstance(parsed.get("fCnt"), (int, float)) else None
        fport_val = int(parsed["fPort"]) if isinstance(parsed.get("fPort"), (int, float)) else None
        confirmed_val = bool(parsed["confirmed"]) if parsed.get("confirmed") is not None else None
        dev_addr = str(parsed["devAddr"]).lower() if parsed.get("devAddr") else None

        data_b64 = parsed.get("data") if isinstance(parsed.get("data"), str) else None
        data_hex: Optional[str] = None
        if data_b64:
            try:
                data_hex = base64.b64decode(data_b64).hex()
            except Exception:
                data_hex = None

        decoded_obj = parsed.get("object") if isinstance(parsed.get("object"), dict) else None

        # Si es un evento status, puede incluir batteryLevel y margin (dB)
        battery_level: Optional[float] = None
        external_power: Optional[bool] = None
        if event_type == "status":
            bat_raw = parsed.get("batteryLevel")
            if isinstance(bat_raw, (int, float)) and not parsed.get("batteryLevelUnavailable", False):
                battery_level = float(bat_raw)
            if parsed.get("externalPowerSource") is not None:
                external_power = bool(parsed.get("externalPowerSource"))
            if best_snr is None and isinstance(parsed.get("margin"), (int, float)):
                best_snr = float(parsed["margin"])

        event_log = {
            "id": str(parsed.get("deduplicationId") or uuid.uuid4()),
            "time": event_time,
            "event_type": event_type,
            "dev_eui": dev_eui,
            "device_name": device_name,
            "application_id": app_id,
            "application_name": app_name,
            "dev_addr": dev_addr,
            "f_cnt": fcnt_val,
            "f_port": fport_val,
            "dr": dr_val,
            "adr": adr_val,
            "confirmed": confirmed_val,
            "rssi": best_rssi,
            "snr": best_snr,
            "frequency_mhz": freq_mhz,
            "spreading_factor": sf_val,
            "bandwidth_khz": bw_khz,
            "gateway_id": best_gw_id,
            "data_base64": data_b64,
            "data_hex": data_hex,
            "decoded_object": decoded_obj,
            "log_level": parsed.get("level") if event_type == "log" else None,
            "log_code": parsed.get("code") if event_type == "log" else None,
            "log_description": parsed.get("description") if event_type == "log" else None,
            "details": parsed,
        }

        frame_log: Optional[Dict[str, Any]] = None
        if event_type in ("up", "join", "down", "txack"):
            if event_type == "up":
                m_type = "ConfirmedDataUp" if confirmed_val else "UnconfirmedDataUp"
                direction = "UPLINK"
            elif event_type == "join":
                m_type = "JoinRequest"
                direction = "UPLINK"
            else:
                m_type = "ConfirmedDataDown" if confirmed_val else "UnconfirmedDataDown"
                direction = "DOWNLINK"

            frame_log = {
                "id": f"frm-{event_log['id']}",
                "time": event_time,
                "direction": direction,
                "m_type": m_type,
                "dev_eui": dev_eui,
                "device_name": device_name,
                "dev_addr": dev_addr,
                "gateway_id": best_gw_id,
                "rx_gateways": rx_gateways,
                "rssi": best_rssi,
                "snr": best_snr,
                "frequency_mhz": freq_mhz,
                "spreading_factor": sf_val,
                "bandwidth_khz": bw_khz,
                "code_rate": code_rate,
                "dr": dr_val,
                "channel": best_channel,
                "adr": adr_val,
                "f_cnt": fcnt_val,
                "f_port": fport_val,
                "confirmed": confirmed_val,
                "phy_payload_hex": data_hex,
                "data_hex": data_hex,
                "data_base64": data_b64,
                "mic_hex": None,
                "tx_info": tx_info_raw,
                "rx_info": rx_info_raw,
                "frame_details": {
                    "mhdr": {"mType": m_type, "major": "LoRaWANR1"},
                    "macPayload": {
                        "fhdr": {
                            "devAddr": dev_addr,
                            "fCtrl": {"adr": adr_val},
                            "fCnt": fcnt_val,
                        },
                        "fPort": fport_val,
                        "frmPayloadHex": data_hex,
                    },
                    "txInfo": tx_info_raw,
                    "rxInfo": rx_info_raw,
                },
            }

        return {
            "kind": "DEVICE_EVENT",
            "event_type": event_type,
            "dev_eui": dev_eui,
            "name": device_name,
            "tenant_id": tenant_id,
            "application_id": app_id,
            "last_seen_at": event_time,
            "battery_level": battery_level,
            "external_power": external_power,
            "rssi": best_rssi,
            "snr": best_snr,
            "dr": dr_val,
            "spreading_factor": sf_val,
            "bandwidth_khz": bw_khz,
            "frequency_mhz": freq_mhz,
            "f_cnt": fcnt_val,
            "f_port": fport_val,
            "dev_addr": dev_addr,
            "adr": adr_val,
            "last_gateway_id": best_gw_id,
            "rx_gateways": rx_gateways,
            "event_log": event_log,
            "frame_log": frame_log,
        }

    # Caso B: Mensajes de Gateway en JSON (stats, up, down)
    if parsed and "/gateway/" in topic:
        parts = topic.split("/")
        gw_id = str(parsed.get("gatewayId") or "").strip().lower()
        if not gw_id and "gateway" in parts:
            idx = parts.index("gateway")
            if idx + 1 < len(parts):
                gw_id = parts[idx + 1].strip().lower()

        phy_b64 = parsed.get("phyPayload")
        frame_log = None
        if isinstance(phy_b64, str) and phy_b64:
            try:
                phy_bytes = base64.b64decode(phy_b64)
                phy_info = parse_lorawan_phy_payload(phy_bytes)
                rx_info = parsed.get("rxInfo") or {}
                tx_info = parsed.get("txInfo") or {}
                rssi_val = int(round(float(rx_info["rssi"]))) if isinstance(rx_info.get("rssi"), (int, float)) else None
                snr_val = round(float(rx_info["snr"]), 1) if isinstance(rx_info.get("snr"), (int, float)) else None
                freq_hz = tx_info.get("frequency")
                freq_mhz = round(float(freq_hz) / 1_000_000.0, 3) if isinstance(freq_hz, (int, float)) and freq_hz > 0 else None
                lora_mod = ((tx_info.get("modulation") or {}).get("lora")) or {}
                sf_val = int(lora_mod["spreadingFactor"]) if isinstance(lora_mod.get("spreadingFactor"), (int, float)) else None
                bw_hz = lora_mod.get("bandwidth")
                bw_khz = int(float(bw_hz) // 1000) if isinstance(bw_hz, (int, float)) and bw_hz > 0 else None
                is_down = "/command/down" in topic
                frame_log = {
                    "id": f"gwfrm-{uuid.uuid4()}",
                    "time": now,
                    "direction": "DOWNLINK" if is_down else "UPLINK",
                    "m_type": phy_info.get("m_type", "Downlink" if is_down else "Uplink"),
                    "dev_eui": phy_info.get("dev_eui"),
                    "device_name": None,
                    "dev_addr": phy_info.get("dev_addr"),
                    "gateway_id": gw_id,
                    "rx_gateways": [{"gateway_id": gw_id, "rssi": rssi_val, "snr": snr_val, "channel": rx_info.get("channel")}] if gw_id else [],
                    "rssi": rssi_val,
                    "snr": snr_val,
                    "frequency_mhz": freq_mhz,
                    "spreading_factor": sf_val,
                    "bandwidth_khz": bw_khz,
                    "code_rate": lora_mod.get("codeRate"),
                    "dr": None,
                    "channel": rx_info.get("channel"),
                    "adr": phy_info.get("adr"),
                    "f_cnt": phy_info.get("f_cnt"),
                    "f_port": phy_info.get("f_port"),
                    "confirmed": phy_info.get("confirmed"),
                    "phy_payload_hex": phy_info.get("phy_payload_hex"),
                    "data_hex": phy_info.get("data_hex"),
                    "data_base64": phy_b64,
                    "mic_hex": phy_info.get("mic_hex"),
                    "tx_info": tx_info,
                    "rx_info": [rx_info] if rx_info else [],
                    "frame_details": parsed,
                }
            except Exception:
                pass

        if gw_id:
            return {
                "kind": "GATEWAY_STATE",
                "gateway_id": gw_id,
                "last_seen_at": now,
                "frame_log": frame_log,
            }

    return None


def sanitize_mqtt_event(topic: str, raw_payload: bytes) -> Optional[Dict[str, Any]]:
    return parse_chirpstack_mqtt_event(topic, raw_payload)


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
            "Content-Type": "application/json",
        }

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if response.is_error:
            msg = response.text
            try:
                err_data = response.json()
                msg = err_data.get("message") or err_data.get("error") or response.text
            except Exception:
                pass
            raise RuntimeError(f"ChirpStack ({response.status_code}): {msg}")

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            verify=settings.chirpstack_verify_ssl, timeout=12.0
        ) as client:
            response = await client.get(
                f"{self.base_url}{path}", headers=self.headers, params=params
            )
            self._check_response(response)
            return response.json()

    async def _post(self, path: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            verify=settings.chirpstack_verify_ssl, timeout=12.0
        ) as client:
            response = await client.post(
                f"{self.base_url}{path}", headers=self.headers, json=json_data
            )
            self._check_response(response)
            return response.json() if response.content else {}

    async def _put(self, path: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            verify=settings.chirpstack_verify_ssl, timeout=12.0
        ) as client:
            response = await client.put(
                f"{self.base_url}{path}", headers=self.headers, json=json_data
            )
            self._check_response(response)
            return response.json() if response.content else {}

    async def _delete(self, path: str) -> None:
        async with httpx.AsyncClient(
            verify=settings.chirpstack_verify_ssl, timeout=12.0
        ) as client:
            response = await client.delete(
                f"{self.base_url}{path}", headers=self.headers
            )
            self._check_response(response)

    # ---------------------------------------------------------------------------
    # TENANTS
    # ---------------------------------------------------------------------------

    async def fetch_tenants(self) -> List[Dict[str, Any]]:
        data = await self._get("/api/tenants", {"limit": 100})
        return [
            {
                "tenant_id": item["id"],
                "name": item.get("name", "Sin nombre"),
                "description": item.get("description", ""),
                "can_have_gateways": bool(item.get("canHaveGateways", True)),
                "max_device_count": int(item.get("maxDeviceCount", 0)),
                "max_gateway_count": int(item.get("maxGatewayCount", 0)),
            }
            for item in data.get("result", [])
        ]

    async def get_tenant(self, tenant_id: str) -> Dict[str, Any]:
        data = await self._get(f"/api/tenants/{tenant_id}")
        t = data.get("tenant") or {}
        return {
            "tenant_id": t.get("id", tenant_id),
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "can_have_gateways": bool(t.get("canHaveGateways", True)),
            "max_device_count": int(t.get("maxDeviceCount", 0)),
            "max_gateway_count": int(t.get("maxGatewayCount", 0)),
        }

    async def create_tenant(
        self,
        name: Any,
        description: str = "",
        can_have_gateways: bool = True,
        max_device_count: int = 0,
        max_gateway_count: int = 0,
    ) -> str:
        if isinstance(name, dict):
            data = name
            name = str(data.get("name", "")).strip()
            description = str(data.get("description") or "").strip()
            can_have_gateways = bool(data.get("can_have_gateways", True))
            max_device_count = int(data.get("max_device_count", 0))
            max_gateway_count = int(data.get("max_gateway_count", 0))
        payload = {
            "tenant": {
                "name": str(name).strip(),
                "description": description or "",
                "canHaveGateways": can_have_gateways,
                "maxDeviceCount": max_device_count,
                "maxGatewayCount": max_gateway_count,
                "privateGatewaysUp": False,
                "privateGatewaysDown": False,
            }
        }
        res = await self._post("/api/tenants", payload)
        return res.get("id", "")

    async def update_tenant(
        self,
        tenant_id: str,
        name: Any,
        description: str = "",
        can_have_gateways: bool = True,
        max_device_count: int = 0,
        max_gateway_count: int = 0,
    ) -> None:
        if isinstance(name, dict):
            data = name
            name = str(data.get("name", "")).strip()
            description = str(data.get("description") or "").strip()
            can_have_gateways = bool(data.get("can_have_gateways", True))
            max_device_count = int(data.get("max_device_count", 0))
            max_gateway_count = int(data.get("max_gateway_count", 0))
        payload = {
            "tenant": {
                "id": tenant_id,
                "name": str(name).strip(),
                "description": description or "",
                "canHaveGateways": can_have_gateways,
                "maxDeviceCount": max_device_count,
                "maxGatewayCount": max_gateway_count,
                "privateGatewaysUp": False,
                "privateGatewaysDown": False,
            }
        }
        await self._put(f"/api/tenants/{tenant_id}", payload)

    async def delete_tenant(self, tenant_id: str) -> None:
        await self._delete(f"/api/tenants/{tenant_id}")

    # ---------------------------------------------------------------------------
    # APPLICATIONS
    # ---------------------------------------------------------------------------

    async def fetch_applications(self, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._get("/api/applications", {"tenantId": tenant_id, "limit": 100})
        return [
            {
                "application_id": item["id"],
                "tenant_id": tenant_id,
                "name": item.get("name", "Sin nombre"),
                "description": item.get("description", ""),
            }
            for item in data.get("result", [])
        ]

    async def get_application(self, application_id: str) -> Dict[str, Any]:
        data = await self._get(f"/api/applications/{application_id}")
        app = data.get("application") or {}
        return {
            "application_id": app.get("id", application_id),
            "tenant_id": app.get("tenantId", ""),
            "name": app.get("name", ""),
            "description": app.get("description", ""),
        }

    async def create_application(
        self,
        name: Any,
        description: str = "",
        tenant_id: str = "",
    ) -> str:
        if isinstance(name, dict):
            data = name
            name = str(data.get("name", "")).strip()
            description = str(data.get("description") or "").strip()
            tenant_id = str(data.get("tenant_id") or "").strip()
        payload = {
            "application": {
                "name": str(name).strip(),
                "description": description or "",
                "tenantId": tenant_id,
            }
        }
        res = await self._post("/api/applications", payload)
        return res.get("id", "")

    async def update_application(self, application_id: str, name: Any, description: str = "") -> None:
        if isinstance(name, dict):
            data = name
            name = str(data.get("name", "")).strip()
            description = str(data.get("description") or "").strip()
        current = await self.get_application(application_id)
        payload = {
            "application": {
                "id": application_id,
                "name": str(name).strip(),
                "description": description or "",
                "tenantId": current["tenant_id"],
            }
        }
        await self._put(f"/api/applications/{application_id}", payload)

    async def delete_application(self, application_id: str) -> None:
        await self._delete(f"/api/applications/{application_id}")

    # ---------------------------------------------------------------------------
    # DEVICE PROFILES
    # ---------------------------------------------------------------------------

    async def fetch_device_profiles(
        self,
        tenant_id: Optional[str] = None,
        include_vendors: bool = True,
    ) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        # 1. Perfiles propios de Tenant (tenantOnly=True)
        tenant_ids: List[str] = []
        if tenant_id:
            tenant_ids = [tenant_id]
        else:
            try:
                tenants = await self.fetch_tenants()
                tenant_ids = [t["tenant_id"] for t in tenants if t.get("tenant_id")]
            except Exception:
                tenant_ids = []

        for tid in tenant_ids:
            try:
                t_data = await self._get(
                    "/api/device-profiles",
                    {"tenantId": tid, "tenantOnly": True, "limit": 200},
                )
            except Exception:
                continue

            for item in t_data.get("result", []):
                pid = item["id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                detail: Dict[str, Any] = {}
                try:
                    detail = await self.get_device_profile(pid)
                except Exception:
                    detail = {}
                profiles.append(
                    {
                        "id": pid,
                        "tenant_id": detail.get("tenant_id") or tid,
                        "name": item.get("name", "Sin nombre").strip(),
                        "description": detail.get("description") or item.get("description", ""),
                        "region": item.get("region", "US915"),
                        "region_config_id": detail.get("region_config_id") or item.get("regionConfigId", ""),
                        "mac_version": item.get("macVersion", "LORAWAN_1_0_3"),
                        "reg_params_revision": item.get("regParamsRevision", "A"),
                        "supports_otaa": bool(item.get("supportsOtaa", True)),
                        "supports_class_b": bool(item.get("supportsClassB", False)),
                        "supports_class_c": bool(item.get("supportsClassC", False)),
                        "uplink_interval": int(detail.get("uplink_interval") or item.get("uplinkInterval") or 3600),
                        "payload_codec_runtime": detail.get("payload_codec_runtime") or item.get("payloadCodecRuntime") or "NONE",
                        "payload_codec_script": "",
                        "vendor_id": item.get("vendorId", "") or "",
                        "vendor_name": item.get("vendorName", "") or "",
                        "device_id": item.get("deviceId", "") or "",
                        "device_name": item.get("deviceName", "") or "",
                        "firmware_version": item.get("firmwareVersion", "") or "",
                        "is_tenant_profile": True,
                        "created_at": item.get("createdAt"),
                        "updated_at": item.get("updatedAt"),
                    }
                )

        # 2. Perfiles globales por Vendor (globalOnly=True)
        if include_vendors:
            try:
                g_data = await self._get(
                    "/api/device-profiles",
                    {"globalOnly": True, "limit": 2000},
                )
                for item in g_data.get("result", []):
                    pid = item["id"]
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    v_name = (item.get("vendorName") or "").strip()
                    profiles.append(
                        {
                            "id": pid,
                            "tenant_id": "",
                            "name": item.get("name", "Sin nombre").strip(),
                            "description": item.get("deviceName", "") or "",
                            "region": item.get("region", "US915"),
                            "region_config_id": item.get("regionConfigId", "") or "",
                            "mac_version": item.get("macVersion", "LORAWAN_1_0_3"),
                            "reg_params_revision": item.get("regParamsRevision", "A"),
                            "supports_otaa": bool(item.get("supportsOtaa", True)),
                            "supports_class_b": bool(item.get("supportsClassB", False)),
                            "supports_class_c": bool(item.get("supportsClassC", False)),
                            "uplink_interval": 3600,
                            "payload_codec_runtime": "JS" if v_name else "NONE",
                            "payload_codec_script": "",
                            "vendor_id": item.get("vendorId", "") or "",
                            "vendor_name": v_name or "Otros Vendors",
                            "device_id": item.get("deviceId", "") or "",
                            "device_name": (item.get("deviceName") or item.get("name") or "").strip(),
                            "firmware_version": item.get("firmwareVersion", "") or "",
                            "is_tenant_profile": False,
                            "created_at": item.get("createdAt"),
                            "updated_at": item.get("updatedAt"),
                        }
                    )
            except Exception:
                pass

        return profiles

    async def get_device_profile(self, profile_id: str) -> Dict[str, Any]:
        data = await self._get(f"/api/device-profiles/{profile_id}")
        dp = data.get("deviceProfile") or {}
        return {
            "id": dp.get("id", profile_id),
            "tenant_id": dp.get("tenantId", ""),
            "name": dp.get("name", ""),
            "description": dp.get("description", ""),
            "region": dp.get("region", "US915"),
            "region_config_id": dp.get("regionConfigId", ""),
            "mac_version": dp.get("macVersion", "LORAWAN_1_0_3"),
            "reg_params_revision": dp.get("regParamsRevision", "A"),
            "adr_algorithm_id": dp.get("adrAlgorithmId", "default"),
            "supports_otaa": bool(dp.get("supportsOtaa", True)),
            "supports_class_b": bool(dp.get("supportsClassB", False)),
            "supports_class_c": bool(dp.get("supportsClassC", False)),
            "uplink_interval": int(dp.get("uplinkInterval", 3600)),
            "payload_codec_runtime": dp.get("payloadCodecRuntime", "NONE"),
            "payload_codec_script": dp.get("payloadCodecScript", ""),
            "created_at": data.get("createdAt"),
            "updated_at": data.get("updatedAt"),
        }

    async def create_device_profile(self, data: Dict[str, Any]) -> str:
        dp_body: Dict[str, Any] = {
            "tenantId": data["tenant_id"],
            "name": data["name"],
            "description": data.get("description", ""),
            "region": data.get("region", "US915"),
            "macVersion": data.get("mac_version", "LORAWAN_1_0_3"),
            "regParamsRevision": data.get("reg_params_revision", "A"),
            "adrAlgorithmId": data.get("adr_algorithm_id", "default"),
            "flushQueueOnActivate": True,
            "uplinkInterval": int(data.get("uplink_interval", 3600)),
            "supportsOtaa": bool(data.get("supports_otaa", True)),
            "supportsClassB": bool(data.get("supports_class_b", False)),
            "supportsClassC": bool(data.get("supports_class_c", False)),
            "autoDetectMeasurements": True,
            "payloadCodecRuntime": data.get("payload_codec_runtime", "NONE"),
            "payloadCodecScript": data.get("payload_codec_script", ""),
        }
        region_cfg = (data.get("region_config_id") or "").strip()
        if region_cfg:
            dp_body["regionConfigId"] = region_cfg
        payload = {"deviceProfile": dp_body}
        res = await self._post("/api/device-profiles", payload)
        return res.get("id", "")

    async def update_device_profile(self, profile_id: str, data: Dict[str, Any]) -> None:
        current = await self.get_device_profile(profile_id)
        dp_body: Dict[str, Any] = {
            "id": profile_id,
            "tenantId": current["tenant_id"],
            "name": data["name"],
            "description": data.get("description", current.get("description", "")),
            "region": data.get("region", current.get("region", "US915")),
            "macVersion": data.get("mac_version", current.get("mac_version", "LORAWAN_1_0_3")),
            "regParamsRevision": data.get("reg_params_revision", current.get("reg_params_revision", "A")),
            "adrAlgorithmId": data.get("adr_algorithm_id", current.get("adr_algorithm_id", "default")),
            "flushQueueOnActivate": True,
            "uplinkInterval": int(data.get("uplink_interval", current.get("uplink_interval", 3600))),
            "supportsOtaa": bool(data.get("supports_otaa", current.get("supports_otaa", True))),
            "supportsClassB": bool(data.get("supports_class_b", current.get("supports_class_b", False))),
            "supportsClassC": bool(data.get("supports_class_c", current.get("supports_class_c", False))),
            "autoDetectMeasurements": True,
            "payloadCodecRuntime": data.get("payload_codec_runtime", current.get("payload_codec_runtime", "NONE")),
            "payloadCodecScript": data.get("payload_codec_script", current.get("payload_codec_script", "")),
        }
        region_cfg = data.get("region_config_id")
        if region_cfg is None:
            region_cfg = current.get("region_config_id", "")
        if region_cfg:
            dp_body["regionConfigId"] = region_cfg.strip()
        payload = {"deviceProfile": dp_body}
        await self._put(f"/api/device-profiles/{profile_id}", payload)

    async def delete_device_profile(self, profile_id: str) -> None:
        await self._delete(f"/api/device-profiles/{profile_id}")

    # ---------------------------------------------------------------------------
    # DEVICES & GATEWAYS (LECTURA EXISTENTE)
    # ---------------------------------------------------------------------------

    async def fetch_devices(self, application_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        data = await self._get("/api/devices", {"applicationId": application_id, "limit": 500})
        devices: List[Dict[str, Any]] = []
        for item in data.get("result", []):
            last_seen = self._parse_iso(item.get("lastSeenAt"))
            created_at = self._parse_iso(item.get("createdAt"))
            status_block = item.get("deviceStatus") or {}
            battery_raw = status_block.get("batteryLevel")
            ext_power = status_block.get("externalPowerSource")
            margin_raw = status_block.get("margin")

            battery_level: Optional[float] = None
            if isinstance(battery_raw, (int, float)) and 0 <= battery_raw <= 100:
                battery_level = float(battery_raw)

            snr_margin: Optional[float] = None
            if isinstance(margin_raw, (int, float)) and -40 <= margin_raw <= 40:
                snr_margin = float(margin_raw)

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
                    "snr_margin": snr_margin,
                    "latitude": None,
                    "longitude": None,
                }
            )
        return devices

    async def get_device_link_metrics(self, dev_eui: str) -> Dict[str, Any]:
        """
        Consulta /api/devices/{dev_eui}/link-metrics en ChirpStack v4 para obtener
        el último RSSI (dBm), SNR (dB), DR y Frecuencia registrados en las últimas 24h.
        """
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = await self._get(
                f"/api/devices/{dev_eui}/link-metrics",
                {"start": start, "end": end, "aggregation": "HOUR"},
            )
        except Exception:
            return {}

        result: Dict[str, Any] = {}

        def _last_non_zero(metric_block: Any) -> Optional[float]:
            if not isinstance(metric_block, dict):
                return None
            datasets = metric_block.get("datasets") or []
            for ds in datasets:
                vals = ds.get("data") or []
                for v in reversed(vals):
                    if isinstance(v, (int, float)) and v != 0:
                        return float(v)
            return None

        rssi_val = _last_non_zero(data.get("gwRssi"))
        if rssi_val is not None:
            result["rssi"] = int(round(rssi_val))

        snr_val = _last_non_zero(data.get("gwSnr"))
        if snr_val is not None:
            result["snr"] = round(snr_val, 1)

        # Extraer último DR usado
        dr_block = data.get("rxPacketsPerDr")
        if isinstance(dr_block, dict):
            for ds in dr_block.get("datasets") or []:
                vals = ds.get("data") or []
                if any(isinstance(v, (int, float)) and v > 0 for v in vals):
                    lbl = str(ds.get("label", "")).replace("DR", "").strip()
                    if lbl.isdigit():
                        result["dr"] = int(lbl)

        # Extraer última frecuencia usada
        freq_block = data.get("rxPacketsPerFreq")
        if isinstance(freq_block, dict):
            for ds in freq_block.get("datasets") or []:
                vals = ds.get("data") or []
                if any(isinstance(v, (int, float)) and v > 0 for v in vals):
                    lbl = str(ds.get("label", "")).strip()
                    try:
                        hz = float(lbl)
                        if hz > 1_000_000:
                            result["frequency_mhz"] = round(hz / 1_000_000.0, 3)
                    except ValueError:
                        pass

        return result

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

    # ---------------------------------------------------------------------------
    # GATEWAYS (GESTIÓN)
    # ---------------------------------------------------------------------------

    async def get_gateway(self, gateway_id: str) -> Dict[str, Any]:
        data = await self._get(f"/api/gateways/{gateway_id}")
        gw = data.get("gateway", {})
        loc = gw.get("location") or {}
        return {
            "gateway_id": gw.get("gatewayId", gateway_id),
            "name": gw.get("name", ""),
            "description": gw.get("description", ""),
            "tenant_id": gw.get("tenantId", ""),
            "stats_interval": gw.get("statsInterval", 30),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "altitude": loc.get("altitude"),
        }

    async def create_gateway(self, data: Dict[str, Any]) -> str:
        loc = {}
        if data.get("latitude") is not None and data.get("longitude") is not None:
            loc = {
                "latitude": float(data["latitude"]),
                "longitude": float(data["longitude"]),
                "altitude": float(data.get("altitude") or 0.0),
            }
        payload = {
            "gateway": {
                "gatewayId": data["gateway_id"].strip().lower(),
                "name": data["name"].strip(),
                "description": data.get("description", ""),
                "tenantId": data["tenant_id"],
                "statsInterval": int(data.get("stats_interval", 30)),
                "location": loc,
            }
        }
        await self._post("/api/gateways", payload)
        return data["gateway_id"].strip().lower()

    async def update_gateway(self, gateway_id: str, data: Dict[str, Any]) -> None:
        current = await self.get_gateway(gateway_id)
        lat = data.get("latitude", current.get("latitude"))
        lon = data.get("longitude", current.get("longitude"))
        loc = {}
        if lat is not None and lon is not None:
            loc = {
                "latitude": float(lat),
                "longitude": float(lon),
                "altitude": float(data.get("altitude", current.get("altitude") or 0.0)),
            }
        payload = {
            "gateway": {
                "gatewayId": gateway_id,
                "name": data["name"].strip(),
                "description": data.get("description", current.get("description", "")),
                "tenantId": current.get("tenant_id", ""),
                "statsInterval": int(data.get("stats_interval", current.get("stats_interval", 30))),
                "location": loc,
            }
        }
        await self._put(f"/api/gateways/{gateway_id}", payload)

    async def delete_gateway(self, gateway_id: str) -> None:
        await self._delete(f"/api/gateways/{gateway_id}")

    # ---------------------------------------------------------------------------
    # DEVICES (GESTIÓN Y LLAVES OTAA)
    # ---------------------------------------------------------------------------

    async def get_device(self, dev_eui: str) -> Dict[str, Any]:
        data = await self._get(f"/api/devices/{dev_eui}")
        d = data.get("device", {})
        return {
            "dev_eui": d.get("devEui", dev_eui),
            "name": d.get("name", ""),
            "description": d.get("description", ""),
            "application_id": d.get("applicationId", ""),
            "device_profile_id": d.get("deviceProfileId", ""),
            "is_disabled": bool(d.get("isDisabled", False)),
            "skip_fcnt_check": bool(d.get("skipFcntCheck", False)),
        }

    async def create_device(self, data: Dict[str, Any]) -> str:
        dev_eui = data["dev_eui"].strip().lower()
        payload = {
            "device": {
                "devEui": dev_eui,
                "name": data["name"].strip(),
                "description": data.get("description", ""),
                "applicationId": data["application_id"],
                "deviceProfileId": data["device_profile_id"],
                "isDisabled": bool(data.get("is_disabled", False)),
                "skipFcntCheck": bool(data.get("skip_fcnt_check", False)),
            }
        }
        await self._post("/api/devices", payload)

        app_key = data.get("app_key")
        if app_key and app_key.strip():
            await self.set_device_keys(dev_eui, app_key.strip().lower())

        return dev_eui

    async def set_device_keys(self, dev_eui: str, app_key: str) -> None:
        payload = {
            "deviceKeys": {
                "devEui": dev_eui.strip().lower(),
                "nwkKey": app_key.strip().lower(),
            }
        }
        try:
            await self._post(f"/api/devices/{dev_eui}/keys", payload)
        except Exception:
            await self._put(f"/api/devices/{dev_eui}/keys", payload)

    async def update_device(self, dev_eui: str, data: Dict[str, Any]) -> None:
        current = await self.get_device(dev_eui)
        payload = {
            "device": {
                "devEui": dev_eui,
                "name": data["name"].strip(),
                "description": data.get("description") if data.get("description") is not None else current.get("description", ""),
                "applicationId": data.get("application_id") or current["application_id"],
                "deviceProfileId": data.get("device_profile_id") or current["device_profile_id"],
                "isDisabled": bool(data["is_disabled"]) if data.get("is_disabled") is not None else current["is_disabled"],
                "skipFcntCheck": bool(data["skip_fcnt_check"]) if data.get("skip_fcnt_check") is not None else current["skip_fcnt_check"],
            }
        }
        await self._put(f"/api/devices/{dev_eui}", payload)

        app_key = data.get("app_key")
        if app_key and app_key.strip():
            await self.set_device_keys(dev_eui, app_key.strip().lower())

    async def delete_device(self, dev_eui: str) -> None:
        await self._delete(f"/api/devices/{dev_eui}")

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
