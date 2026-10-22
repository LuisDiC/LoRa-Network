from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.auth import hash_password, verify_password
from app.config import settings
from app.schemas import (
    AlarmDTO,
    AlarmRuleUpdateDTO,
    ApplicationCreateRequest,
    ApplicationDTO,
    ApplicationUpdateRequest,
    DashboardSummaryDTO,
    DeviceCreateRequest,
    DeviceProfileCreateRequest,
    DeviceProfileDTO,
    DeviceProfileUpdateRequest,
    DeviceUpdateRequest,
    GatewayAlertConfigDTO,
    GatewayAlertConfigUpdateDTO,
    GatewayCreateRequest,
    GatewayMetadataDTO,
    GatewayUpdateRequest,
    LocationUpdateRequest,
    LoginRequest,
    NodeAlertConfigDTO,
    NodeAlertConfigUpdateDTO,
    NodeMetadataDTO,
    NotificationPreferencesDTO,
    OperationalEventDTO,
    TenantCreateRequest,
    TenantDTO,
    TenantUpdateRequest,
    TokenResponse,
    UserCreateRequest,
    UserPasswordChangeRequest,
    UserResponseDTO,
)
from app.services.alarm_engine import OperationalAlarmEngine, SEVERITY_RANK
from app.services.chirpstack_client import (
    ChirpStackV4Client,
    evaluate_communication_state,
    format_battery,
    format_location,
)
from app.services.push_service import PushNotificationService

logger = logging.getLogger("lorawan_monitor.api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sincronización inmediata al arrancar el backend con ChirpStack
    try:
        await store.sync_from_chirpstack()
    except Exception as e:
        logger.error("Error en sincronización inicial con ChirpStack: %s", e)

    async def sync_loop():
        while True:
            await asyncio.sleep(settings.sync_interval_seconds)
            try:
                await store.sync_from_chirpstack()
            except Exception as e:
                logger.error("Error en sincronización con ChirpStack: %s", e)

    sync_task = asyncio.create_task(sync_loop())
    yield
    sync_task.cancel()

app = FastAPI(
    title="LoRaWAN Monitor Operational API",
    version="1.0.0",
    description="API de supervisión de metadatos y conectividad ChirpStack v4 (Sin telemetría de sensores)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
alarm_engine = OperationalAlarmEngine()
push_service = PushNotificationService()
chirpstack_client = ChirpStackV4Client()


class RuntimeStore:
    def __init__(self) -> None:
        self.chirpstack_connected: bool = True
        self.chirpstack_status_detail: str = "Conectado a ChirpStack v4"
        self.last_sync_at: datetime = datetime.now(timezone.utc)
        self.rules_file = Path("data/alarm_rules.json")
        self.rules: Dict[str, AlarmRuleUpdateDTO] = self._load_rules()
        self.user_prefs: Dict[str, NotificationPreferencesDTO] = {
            "operador.demo": NotificationPreferencesDTO(
                push_enabled=True,
                notify_critical=True,
                notify_high=True,
                notify_medium=True,
                notify_low=False,
                notify_node_silent=True,
                notify_gateway_offline=True,
                notify_low_battery=True,
                notify_chirpstack_loss=True,
                notify_recovery=True,
            )
        }
        self.push_tokens: Dict[str, List[str]] = {}
        self.custom_locations_file = Path("data/custom_locations.json")
        self.custom_locations: Dict[str, Dict[str, float]] = self._load_custom_locations()
        self.node_alerts_file = Path("data/node_alerts.json")
        self.gw_alerts_file = Path("data/gateway_alerts.json")
        self.node_alerts: Dict[str, Dict[str, Any]] = self._load_node_alerts()
        self.gw_alerts: Dict[str, Dict[str, Any]] = self._load_gw_alerts()
        self.tenants: List[Dict] = []
        self.applications: List[Dict] = []
        self.raw_nodes: List[Dict] = []
        self.raw_gateways: List[Dict] = []
        self.ws_clients: List[WebSocket] = []
        self.evaluate_all()

    def get_user_prefs(self, username: str) -> NotificationPreferencesDTO:
        if username not in self.user_prefs:
            self.user_prefs[username] = NotificationPreferencesDTO(
                push_enabled=True,
                notify_critical=True,
                notify_high=True,
                notify_medium=True,
                notify_low=False,
                notify_node_silent=True,
                notify_gateway_offline=True,
                notify_low_battery=True,
                notify_chirpstack_loss=True,
                notify_recovery=True,
            )
        return self.user_prefs[username]

    def _load_rules(self) -> Dict[str, AlarmRuleUpdateDTO]:
        defaults = {
            "NODE_SILENT": AlarmRuleUpdateDTO(
                code="NODE_SILENT", severity="CRITICAL", threshold_value=45, unit="seconds", tolerance_minutes=0, is_enabled=True
            ),
            "GATEWAY_OFFLINE": AlarmRuleUpdateDTO(
                code="GATEWAY_OFFLINE", severity="HIGH", threshold_value=15, unit="minutes", tolerance_minutes=2, is_enabled=True
            ),
            "LOW_BATTERY": AlarmRuleUpdateDTO(
                code="LOW_BATTERY", severity="HIGH", threshold_value=15, unit="%", tolerance_minutes=0, is_enabled=True
            ),
            "CHIRPSTACK_DISCONNECTED": AlarmRuleUpdateDTO(
                code="CHIRPSTACK_DISCONNECTED", severity="CRITICAL", threshold_value=1, unit="minutes", tolerance_minutes=0, is_enabled=True
            ),
        }
        try:
            if self.rules_file.exists():
                with open(self.rules_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        for k, v in raw.items():
                            if isinstance(v, dict):
                                defaults[k] = AlarmRuleUpdateDTO(**v)
        except Exception as e:
            logger.warning("No se pudo cargar alarm_rules.json: %s", e)
        return defaults

    def _save_rules(self) -> None:
        try:
            self.rules_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.rules_file, "w", encoding="utf-8") as f:
                json.dump({k: v.model_dump() for k, v in self.rules.items()}, f, indent=2)
        except Exception as e:
            logger.error("Error guardando alarm_rules.json: %s", e)

    def _load_custom_locations(self) -> Dict[str, Dict[str, float]]:
        try:
            if self.custom_locations_file.exists():
                with open(self.custom_locations_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("No se pudo cargar custom_locations.json: %s", e)
        return {}

    def _save_custom_locations(self) -> None:
        try:
            self.custom_locations_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.custom_locations_file, "w", encoding="utf-8") as f:
                json.dump(self.custom_locations, f, indent=2)
        except Exception as e:
            logger.error("Error guardando custom_locations.json: %s", e)

    def _load_node_alerts(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self.node_alerts_file.exists():
                with open(self.node_alerts_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("No se pudo cargar node_alerts.json: %s", e)
        return {}

    def _save_node_alerts(self) -> None:
        try:
            self.node_alerts_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.node_alerts_file, "w", encoding="utf-8") as f:
                json.dump(self.node_alerts, f, indent=2)
        except Exception as e:
            logger.error("Error guardando node_alerts.json: %s", e)

    def _load_gw_alerts(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self.gw_alerts_file.exists():
                with open(self.gw_alerts_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("No se pudo cargar gateway_alerts.json: %s", e)
        return {}

    def _save_gw_alerts(self) -> None:
        try:
            self.gw_alerts_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.gw_alerts_file, "w", encoding="utf-8") as f:
                json.dump(self.gw_alerts, f, indent=2)
        except Exception as e:
            logger.error("Error guardando gateway_alerts.json: %s", e)

    async def sync_from_chirpstack(self) -> bool:
        if not settings.chirpstack_api_token:
            self.chirpstack_connected = False
            self.chirpstack_status_detail = "Esperando API Token de ChirpStack v4 en .env"
            self.evaluate_all()
            return False

        try:
            tenants = await chirpstack_client.fetch_tenants()
            apps_all: List[Dict] = []
            nodes_all: List[Dict] = []
            tenant_map = {t["tenant_id"]: t["name"] for t in tenants}

            for t in tenants:
                tid = t["tenant_id"]
                tname = t["name"]
                apps = await chirpstack_client.fetch_applications(tid)
                for a in apps:
                    a["tenant_name"] = tname
                    apps_all.append(a)
                    devs = await chirpstack_client.fetch_devices(a["application_id"], tid)
                    for d in devs:
                        d["application_name"] = a["name"]
                        d["tenant_name"] = tname
                        nodes_all.append(d)

            gateways = await chirpstack_client.fetch_gateways()
            for g in gateways:
                tid = g.get("tenant_id")
                g["tenant_name"] = tenant_map.get(tid, "No asignado")

            self.tenants = tenants
            self.applications = apps_all
            self.raw_nodes = nodes_all
            self.raw_gateways = gateways
            self.chirpstack_connected = True
            self.chirpstack_status_detail = "Conectado a ChirpStack v4 (Servicio en vivo)"
            self.last_sync_at = datetime.now(timezone.utc)
            self.evaluate_all()
            logger.info("ChirpStack sync completado: %d tenants, %d apps, %d nodos, %d gateways", len(tenants), len(apps_all), len(nodes_all), len(gateways))
            return True
        except Exception as exc:
            logger.warning("Fallo al conectar con ChirpStack: %s", exc)
            self.chirpstack_connected = False
            self.chirpstack_status_detail = f"Sin conexión a ChirpStack ({str(exc)[:50]})"
            self.evaluate_all()
            return False

    def evaluate_all(self) -> Tuple[List[NodeMetadataDTO], List[GatewayMetadataDTO]]:
        now = datetime.now(timezone.utc)
        node_rule = self.rules["NODE_SILENT"]
        gw_rule = self.rules["GATEWAY_OFFLINE"]
        bat_rule = self.rules["LOW_BATTERY"]

        nodes_dto: List[NodeMetadataDTO] = []
        for raw in self.raw_nodes:
            dev_eui = raw["dev_eui"]
            dev_eui_lower = dev_eui.lower()
            if dev_eui in self.custom_locations:
                raw["latitude"] = self.custom_locations[dev_eui].get("latitude")
                raw["longitude"] = self.custom_locations[dev_eui].get("longitude")
            elif dev_eui_lower in self.custom_locations:
                raw["latitude"] = self.custom_locations[dev_eui_lower].get("latitude")
                raw["longitude"] = self.custom_locations[dev_eui_lower].get("longitude")

            node_cfg = self.node_alerts.get(dev_eui_lower) or self.node_alerts.get(dev_eui)
            if node_cfg:
                inact_enabled = bool(node_cfg.get("inactivity_enabled", node_rule.is_enabled))
                inact_val = int(node_cfg.get("inactivity_seconds", 60))
                inact_unit = "seconds"
                bat_enabled = bool(node_cfg.get("low_battery_enabled", bat_rule.is_enabled))
                bat_thresh = int(node_cfg.get("low_battery_threshold", bat_rule.threshold_value))
            else:
                inact_enabled = node_rule.is_enabled
                inact_val = node_rule.threshold_value
                inact_unit = getattr(node_rule, "unit", "minutes") or "minutes"
                bat_enabled = bat_rule.is_enabled
                bat_thresh = bat_rule.threshold_value

            state, label, criteria, mins = evaluate_communication_state(
                raw["last_seen_at"], inact_val, now, unit=inact_unit
            )
            secs_since = (
                int(max(0.0, (now - raw["last_seen_at"]).total_seconds()))
                if raw.get("last_seen_at")
                else None
            )
            has_loc, loc_display = format_location(raw["latitude"], raw["longitude"])
            bat_display = format_battery(raw["battery_level"], raw["external_power"])
            reg_display = (
                raw["registered_at"].strftime("%Y-%m-%d %H:%M UTC")
                if raw.get("registered_at")
                else "No disponible"
            )

            # Evaluación de alarma de desconexión de nodo (respeta alerta individual o regla global)
            if state == "NO_COMMUNICATION" and inact_enabled and mins is not None:
                if inact_unit == "seconds":
                    elapsed_str = f"{secs_since if secs_since is not None else mins * 60}s"
                    thresh_str = f"{inact_val}s"
                else:
                    elapsed_str = f"{mins} min"
                    thresh_str = f"{inact_val} min"

                alarm_engine.trigger_or_update(
                    rule_code="NODE_SILENT",
                    severity=node_rule.severity,
                    entity_type="NODE",
                    entity_id=raw["dev_eui"],
                    entity_name=raw["name"],
                    title="Nodo sin comunicación",
                    condition_description=(
                        f"El nodo no ha transmitido uplinks durante {elapsed_str} "
                        f"(umbral configurado: {thresh_str})."
                    ),
                    observed_value=f"Sin actividad durante {elapsed_str}",
                    threshold_formatted=thresh_str,
                    last_activity_at=raw["last_seen_at"],
                    now=now,
                )
            elif state == "ACTIVE" or not inact_enabled:
                alarm_engine.resolve_if_recovered(
                    "NODE_SILENT", "NODE", raw["dev_eui"], "Comunicación de nodo restablecida", now
                )

            # Evaluación de alarma de batería baja (solo si existe dato fiable)
            bat_val = raw["battery_level"]
            if (
                bat_val is not None
                and bat_enabled
                and bat_val <= bat_thresh
            ):
                alarm_engine.trigger_or_update(
                    rule_code="LOW_BATTERY",
                    severity=bat_rule.severity,
                    entity_type="NODE",
                    entity_id=raw["dev_eui"],
                    entity_name=raw["name"],
                    title="Batería en nivel crítico",
                    condition_description=(
                        f"El nivel energético reportado por el nodo es {int(bat_val)}%, "
                        f"inferior al umbral operativo de {bat_thresh}%."
                    ),
                    observed_value=f"Batería {int(bat_val)}%",
                    threshold_formatted=f"{bat_thresh}%",
                    last_activity_at=raw["last_seen_at"],
                    now=now,
                )
            elif (bat_val is not None and bat_val > bat_thresh) or not bat_enabled:
                alarm_engine.resolve_if_recovered(
                    "LOW_BATTERY", "NODE", raw["dev_eui"], f"Batería recuperada a {int(bat_val or 0)}%", now
                )

            # Verificar si el nodo tiene alarmas activas asociadas
            node_alarms = [
                a
                for a in alarm_engine.active_alarms.values()
                if a.entity_type == "NODE" and a.entity_id == raw["dev_eui"]
            ]
            highest_sev = None
            if node_alarms:
                node_alarms.sort(key=lambda a: SEVERITY_RANK[a.severity], reverse=True)
                highest_sev = node_alarms[0].severity

            nodes_dto.append(
                NodeMetadataDTO(
                    dev_eui=raw["dev_eui"],
                    name=raw["name"],
                    application_id=raw["application_id"],
                    application_name=raw["application_name"],
                    tenant_id=raw["tenant_id"],
                    tenant_name=raw["tenant_name"],
                    communication_state=state,
                    state_label=label,
                    state_criteria=criteria,
                    last_seen_at=raw["last_seen_at"],
                    minutes_since_last_seen=mins,
                    seconds_since_last_seen=secs_since,
                    battery_level=raw["battery_level"],
                    battery_display=bat_display,
                    location_available=has_loc,
                    latitude=raw["latitude"] if has_loc else None,
                    longitude=raw["longitude"] if has_loc else None,
                    location_display=loc_display,
                    registered_at=raw["registered_at"],
                    registered_display=reg_display,
                    has_active_alarm=bool(node_alarms),
                    highest_alarm_severity=highest_sev,
                )
            )

        gateways_dto: List[GatewayMetadataDTO] = []
        for gw in self.raw_gateways:
            gw_id = gw["gateway_id"]
            gw_id_lower = gw_id.lower()
            if gw_id in self.custom_locations:
                gw["latitude"] = self.custom_locations[gw_id].get("latitude")
                gw["longitude"] = self.custom_locations[gw_id].get("longitude")
            elif gw_id_lower in self.custom_locations:
                gw["latitude"] = self.custom_locations[gw_id_lower].get("latitude")
                gw["longitude"] = self.custom_locations[gw_id_lower].get("longitude")

            gw_cfg = self.gw_alerts.get(gw_id_lower) or self.gw_alerts.get(gw_id)
            if gw_cfg:
                gw_enabled = bool(gw_cfg.get("offline_enabled", gw_rule.is_enabled))
                gw_val = int(gw_cfg.get("offline_seconds", 900))
                gw_unit = "seconds"
            else:
                gw_enabled = gw_rule.is_enabled
                gw_val = gw_rule.threshold_value
                gw_unit = getattr(gw_rule, "unit", "minutes") or "minutes"

            state, label, criteria, mins = evaluate_communication_state(
                gw["last_seen_at"], gw_val, now, unit=gw_unit
            )
            gw_secs_since = (
                int(max(0.0, (now - gw["last_seen_at"]).total_seconds()))
                if gw.get("last_seen_at")
                else None
            )
            has_loc, loc_display = format_location(gw["latitude"], gw["longitude"])

            if state == "NO_COMMUNICATION" and gw_enabled and mins is not None:
                if gw_unit == "seconds":
                    gw_elapsed_str = f"{gw_secs_since if gw_secs_since is not None else mins * 60}s"
                    gw_thresh_str = f"{gw_val}s"
                else:
                    gw_elapsed_str = f"{mins} min"
                    gw_thresh_str = f"{gw_val} min"

                alarm_engine.trigger_or_update(
                    rule_code="GATEWAY_OFFLINE",
                    severity=gw_rule.severity,
                    entity_type="GATEWAY",
                    entity_id=gw["gateway_id"],
                    entity_name=gw["name"],
                    title="Gateway sin comunicación",
                    condition_description=(
                        f"El gateway no ha reportado estado operativo durante {gw_elapsed_str} "
                        f"(umbral configurado: {gw_thresh_str})."
                    ),
                    observed_value=f"Inactivo hace {gw_elapsed_str}",
                    threshold_formatted=gw_thresh_str,
                    last_activity_at=gw["last_seen_at"],
                    now=now,
                )
            elif state == "ACTIVE" or not gw_enabled:
                alarm_engine.resolve_if_recovered(
                    "GATEWAY_OFFLINE", "GATEWAY", gw["gateway_id"], "Gateway nuevamente en línea", now
                )

            gw_alarms = [
                a
                for a in alarm_engine.active_alarms.values()
                if a.entity_type == "GATEWAY" and a.entity_id == gw["gateway_id"]
            ]

            gateways_dto.append(
                GatewayMetadataDTO(
                    gateway_id=gw["gateway_id"],
                    name=gw["name"],
                    tenant_id=gw["tenant_id"],
                    tenant_name=gw.get("tenant_name") or "No disponible",
                    communication_state=state,
                    state_label=label,
                    state_criteria=criteria,
                    last_seen_at=gw["last_seen_at"],
                    minutes_since_last_seen=mins,
                    seconds_since_last_seen=gw_secs_since,
                    location_available=has_loc,
                    latitude=gw["latitude"] if has_loc else None,
                    longitude=gw["longitude"] if has_loc else None,
                    location_display=loc_display,
                    has_active_alarm=bool(gw_alarms),
                )
            )

        return nodes_dto, gateways_dto


store = RuntimeStore()

USERS_FILE = Path("data/users.json")


def load_users() -> Dict[str, Dict[str, Any]]:
    default_users = {
        "operador.demo": {
            "username": "operador.demo",
            "full_name": "Operador de Red",
            "role": "Administrador LoRaWAN",
            "password_sha256": hashlib.sha256("demopassword".encode()).hexdigest(),
            "created_at": "2024-01-01T00:00:00Z",
        }
    }
    try:
        if USERS_FILE.exists():
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
    except Exception as exc:
        logger.warning("No se pudo cargar users.json: %s", exc)

    save_users(default_users)
    return default_users


def save_users(users: Dict[str, Dict[str, Any]]) -> None:
    try:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
    except Exception as exc:
        logger.error("Error guardando users.json: %s", exc)


USERS_DB = load_users()


def verify_and_upgrade_password(user: Dict[str, Any], plain_password: str) -> bool:
    """
    Verifica la contraseña usando bcrypt (password_hash) o SHA-256 legacy (password_sha256).
    Si coincide con un hash SHA-256 legacy, migra automáticamente a bcrypt con salt.
    """
    bcrypt_hash = user.get("password_hash")
    if bcrypt_hash:
        try:
            if verify_password(plain_password, bcrypt_hash):
                return True
        except Exception:
            pass

    legacy_sha = user.get("password_sha256")
    if legacy_sha:
        digest = hashlib.sha256(plain_password.encode()).hexdigest()
        if hmac.compare_digest(legacy_sha, digest):
            try:
                user["password_hash"] = hash_password(plain_password)
                save_users(USERS_DB)
            except Exception:
                pass
            return True
    return False


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, str]:
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        username: Optional[str] = payload.get("sub")
        if not username or username not in USERS_DB:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
        return USERS_DB[username]
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión expirada") from exc


def require_admin(current_user: Dict[str, str] = Depends(get_current_user)) -> Dict[str, str]:
    if current_user.get("role") != "Administrador LoRaWAN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta acción requiere permisos de Administrador LoRaWAN",
        )
    return current_user


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest) -> TokenResponse:
    user = USERS_DB.get(req.username.strip().lower())
    if not user or not verify_and_upgrade_password(user, req.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    token = jwt.encode(
        {"sub": user["username"], "exp": expire},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return TokenResponse(
        access_token=token,
        user_full_name=user["full_name"],
        role=user["role"],
        demo_mode=settings.demo_mode,
    )


@app.get("/api/users", response_model=List[UserResponseDTO])
async def list_users(_: Dict = Depends(get_current_user)) -> List[UserResponseDTO]:
    return [
        UserResponseDTO(
            username=u["username"],
            full_name=u["full_name"],
            role=u["role"],
            created_at=u.get("created_at"),
        )
        for u in USERS_DB.values()
    ]


@app.post("/api/users", response_model=UserResponseDTO, status_code=status.HTTP_201_CREATED)
async def create_user(
    req: UserCreateRequest,
    current_user: Dict = Depends(require_admin),
) -> UserResponseDTO:
    clean_username = req.username.strip().lower()
    if clean_username in USERS_DB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El nombre de usuario '{clean_username}' ya existe",
        )

    hashed_sha = hashlib.sha256(req.password.encode()).hexdigest()
    try:
        hashed_bcrypt = hash_password(req.password)
    except Exception:
        hashed_bcrypt = ""
    created_now = datetime.now(timezone.utc).isoformat()

    new_user = {
        "username": clean_username,
        "full_name": req.full_name.strip(),
        "role": req.role.strip(),
        "password_hash": hashed_bcrypt,
        "password_sha256": hashed_sha,
        "created_at": created_now,
    }
    USERS_DB[clean_username] = new_user
    save_users(USERS_DB)

    # Inicializar preferencias de notificaciones por defecto
    store.get_user_prefs(clean_username)

    logger.info("Usuario creado: %s por %s", clean_username, current_user.get("username"))
    return UserResponseDTO(
        username=new_user["username"],
        full_name=new_user["full_name"],
        role=new_user["role"],
        created_at=new_user["created_at"],
    )


@app.put("/api/users/{username}/password")
async def change_user_password(
    username: str,
    req: UserPasswordChangeRequest,
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, str]:
    clean_username = username.strip().lower()
    if clean_username not in USERS_DB:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    if current_user["username"] != clean_username and current_user.get("role") != "Administrador LoRaWAN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para modificar la contraseña de este usuario",
        )

    USERS_DB[clean_username]["password_sha256"] = hashlib.sha256(req.new_password.encode()).hexdigest()
    try:
        USERS_DB[clean_username]["password_hash"] = hash_password(req.new_password)
    except Exception:
        pass
    save_users(USERS_DB)
    logger.info("Contraseña actualizada para usuario: %s", clean_username)
    return {"message": f"Contraseña actualizada exitosamente para '{clean_username}'"}


@app.delete("/api/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    username: str,
    current_user: Dict = Depends(get_current_user),
) -> None:
    if current_user.get("role") != "Administrador LoRaWAN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden eliminar usuarios",
        )

    clean_username = username.strip().lower()
    if clean_username == current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propia cuenta en sesión activa",
        )

    if clean_username not in USERS_DB:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    del USERS_DB[clean_username]
    save_users(USERS_DB)
    logger.info("Usuario eliminado: %s por %s", clean_username, current_user.get("username"))


@app.get("/api/dashboard", response_model=DashboardSummaryDTO)
@app.get("/api/dashboard/summary", response_model=DashboardSummaryDTO)
async def get_dashboard(_: Dict = Depends(get_current_user)) -> DashboardSummaryDTO:
    nodes, gateways = store.evaluate_all()
    now = datetime.now(timezone.utc)
    is_stale = (now - store.last_sync_at).total_seconds() > (settings.sync_interval_seconds * 2)

    active_alarms = list(alarm_engine.active_alarms.values())
    low_bat_threshold = store.rules["LOW_BATTERY"].threshold_value

    return DashboardSummaryDTO(
        chirpstack_connected=store.chirpstack_connected,
        chirpstack_status_detail=store.chirpstack_status_detail,
        last_sync_at=store.last_sync_at,
        is_stale=is_stale,
        demo_mode=settings.demo_mode,
        total_nodes=len(nodes),
        active_nodes=sum(1 for n in nodes if n.communication_state == "ACTIVE"),
        silent_nodes=sum(1 for n in nodes if n.communication_state == "NO_COMMUNICATION"),
        unknown_nodes=sum(1 for n in nodes if n.communication_state == "UNKNOWN"),
        low_battery_nodes=sum(
            1 for n in nodes if n.battery_level is not None and n.battery_level <= low_bat_threshold
        ),
        total_gateways=len(gateways),
        active_gateways=sum(1 for g in gateways if g.communication_state == "ACTIVE"),
        inactive_gateways=sum(1 for g in gateways if g.communication_state != "ACTIVE"),
        accessible_tenants=len(store.tenants),
        accessible_applications=len(store.applications),
        alarms_total_active=len(active_alarms),
        alarms_critical=sum(1 for a in active_alarms if a.severity == "CRITICAL"),
        alarms_high=sum(1 for a in active_alarms if a.severity == "HIGH"),
        alarms_medium=sum(1 for a in active_alarms if a.severity == "MEDIUM"),
        alarms_low=sum(1 for a in active_alarms if a.severity == "LOW"),
        highest_priority_alarm=alarm_engine.get_highest_priority_alarm(),
        recent_events=alarm_engine.events[:10],
    )


@app.post("/api/sync")
async def trigger_manual_sync(_: Dict = Depends(get_current_user)) -> Dict[str, Any]:
    success = await store.sync_from_chirpstack()
    return {
        "success": success,
        "detail": store.chirpstack_status_detail,
        "nodes_count": len(store.raw_nodes),
        "gateways_count": len(store.raw_gateways),
        "last_sync_at": store.last_sync_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CRUD: NODOS / DISPOSITIVOS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/nodes", response_model=NodeMetadataDTO, status_code=status.HTTP_201_CREATED)
@app.post("/api/devices", response_model=NodeMetadataDTO, status_code=status.HTTP_201_CREATED)
async def create_node(
    req: DeviceCreateRequest,
    _: Dict = Depends(get_current_user),
) -> NodeMetadataDTO:
    clean_eui = req.dev_eui.strip().lower()
    try:
        await chirpstack_client.create_device(req.model_dump())
        if req.latitude is not None and req.longitude is not None:
            store.custom_locations[clean_eui] = {"latitude": req.latitude, "longitude": req.longitude}
            store._save_custom_locations()
        await store.sync_from_chirpstack()
        nodes, _ = store.evaluate_all()
        for node in nodes:
            if node.dev_eui.lower() == clean_eui:
                return node
        has_loc = req.latitude is not None and req.longitude is not None
        return NodeMetadataDTO(
            dev_eui=clean_eui,
            name=req.name,
            application_id=req.application_id,
            application_name="Aplicación",
            tenant_id="",
            tenant_name="",
            communication_state="UNKNOWN",
            state_label="Estado desconocido",
            state_criteria="Recién registrado",
            battery_display="No disponible",
            location_available=has_loc,
            latitude=req.latitude,
            longitude=req.longitude,
            location_display=f"{req.latitude:.5f}, {req.longitude:.5f}" if has_loc else "No disponible",
            registered_display="Recién registrado",
            has_active_alarm=False,
        )
    except Exception as exc:
        logger.error("Error creando dispositivo en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/nodes/{dev_eui}", response_model=NodeMetadataDTO)
@app.put("/api/devices/{dev_eui}", response_model=NodeMetadataDTO)
async def update_node(
    dev_eui: str,
    req: DeviceUpdateRequest,
    _: Dict = Depends(get_current_user),
) -> NodeMetadataDTO:
    clean_eui = dev_eui.strip().lower()
    try:
        await chirpstack_client.update_device(clean_eui, req.model_dump())
        await store.sync_from_chirpstack()
        nodes, _ = store.evaluate_all()
        for node in nodes:
            if node.dev_eui.lower() == clean_eui:
                return node
        raise HTTPException(status_code=404, detail="Nodo no encontrado")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error actualizando dispositivo en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/nodes/{dev_eui}", status_code=status.HTTP_204_NO_CONTENT)
@app.delete("/api/devices/{dev_eui}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    dev_eui: str,
    _: Dict = Depends(get_current_user),
) -> None:
    clean_eui = dev_eui.strip().lower()
    try:
        await chirpstack_client.delete_device(clean_eui)
        if clean_eui in store.custom_locations:
            del store.custom_locations[clean_eui]
            store._save_custom_locations()
        if clean_eui in store.node_alerts:
            del store.node_alerts[clean_eui]
            store._save_node_alerts()
        await store.sync_from_chirpstack()
    except Exception as exc:
        logger.error("Error eliminando dispositivo en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# CRUD: GATEWAYS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/gateways", response_model=GatewayMetadataDTO, status_code=status.HTTP_201_CREATED)
async def create_gateway(
    req: GatewayCreateRequest,
    _: Dict = Depends(get_current_user),
) -> GatewayMetadataDTO:
    clean_id = req.gateway_id.strip().lower()
    try:
        await chirpstack_client.create_gateway(req.model_dump())
        if req.latitude is not None and req.longitude is not None:
            store.custom_locations[clean_id] = {"latitude": req.latitude, "longitude": req.longitude}
            store._save_custom_locations()
        await store.sync_from_chirpstack()
        _, gateways = store.evaluate_all()
        for gw in gateways:
            if gw.gateway_id.lower() == clean_id:
                return gw
        return GatewayMetadataDTO(
            gateway_id=clean_id,
            name=req.name,
            tenant_id=req.tenant_id,
            tenant_name="Tenant",
            communication_state="UNKNOWN",
            state_label="Estado desconocido",
            state_criteria="Recién registrado",
            location_available=bool(req.latitude and req.longitude),
            latitude=req.latitude,
            longitude=req.longitude,
            location_display=f"{req.latitude:.5f}, {req.longitude:.5f}" if req.latitude and req.longitude else "No disponible",
            has_active_alarm=False,
        )
    except Exception as exc:
        logger.error("Error creando gateway en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/gateways/{gateway_id}", response_model=GatewayMetadataDTO)
async def update_gateway(
    gateway_id: str,
    req: GatewayUpdateRequest,
    _: Dict = Depends(get_current_user),
) -> GatewayMetadataDTO:
    clean_id = gateway_id.strip().lower()
    try:
        await chirpstack_client.update_gateway(clean_id, req.model_dump())
        if req.latitude is not None and req.longitude is not None:
            store.custom_locations[clean_id] = {"latitude": req.latitude, "longitude": req.longitude}
            store._save_custom_locations()
        await store.sync_from_chirpstack()
        _, gateways = store.evaluate_all()
        for gw in gateways:
            if gw.gateway_id.lower() == clean_id:
                return gw
        raise HTTPException(status_code=404, detail="Gateway no encontrado")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error actualizando gateway en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gateway(
    gateway_id: str,
    _: Dict = Depends(get_current_user),
) -> None:
    clean_id = gateway_id.strip().lower()
    try:
        await chirpstack_client.delete_gateway(clean_id)
        if clean_id in store.custom_locations:
            del store.custom_locations[clean_id]
            store._save_custom_locations()
        if clean_id in store.gw_alerts:
            del store.gw_alerts[clean_id]
            store._save_gw_alerts()
        await store.sync_from_chirpstack()
    except Exception as exc:
        logger.error("Error eliminando gateway en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# CRUD: TENANTS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/tenants", response_model=TenantDTO, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    req: TenantCreateRequest,
    _: Dict = Depends(get_current_user),
) -> TenantDTO:
    try:
        tid = await chirpstack_client.create_tenant(req.model_dump())
        await store.sync_from_chirpstack()
        for t in store.tenants:
            if t["tenant_id"] == tid:
                return TenantDTO(
                    tenant_id=t["tenant_id"],
                    name=t["name"],
                    description=t.get("description", ""),
                    can_have_gateways=t.get("can_have_gateways", True),
                    max_device_count=t.get("max_device_count", 0),
                    max_gateway_count=t.get("max_gateway_count", 0),
                    applications_count=0,
                    nodes_count=0,
                    gateways_count=0,
                )
        return TenantDTO(tenant_id=tid, name=req.name, description=req.description or "", applications_count=0, nodes_count=0, gateways_count=0)
    except Exception as exc:
        logger.error("Error creando tenant en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(tenant_id: str, _: Dict = Depends(require_admin)) -> None:
    try:
        await chirpstack_client.delete_tenant(tenant_id)
        await store.sync_from_chirpstack()
    except Exception as exc:
        logger.error("Error eliminando tenant en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# CRUD: APLICACIONES
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/applications", response_model=ApplicationDTO, status_code=status.HTTP_201_CREATED)
async def create_application(
    req: ApplicationCreateRequest,
    _: Dict = Depends(get_current_user),
) -> ApplicationDTO:
    try:
        aid = await chirpstack_client.create_application(req.model_dump())
        await store.sync_from_chirpstack()
        for a in store.applications:
            if a["application_id"] == aid:
                return ApplicationDTO(
                    application_id=a["application_id"],
                    name=a["name"],
                    description=a.get("description", ""),
                    tenant_id=a["tenant_id"],
                    tenant_name=a.get("tenant_name", "Tenant"),
                    nodes_count=0,
                    active_nodes_count=0,
                )
        return ApplicationDTO(application_id=aid, name=req.name, description=req.description or "", tenant_id=req.tenant_id, tenant_name="Tenant", nodes_count=0, active_nodes_count=0)
    except Exception as exc:
        logger.error("Error creando aplicación en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(application_id: str, _: Dict = Depends(require_admin)) -> None:
    try:
        await chirpstack_client.delete_application(application_id)
        await store.sync_from_chirpstack()
    except Exception as exc:
        logger.error("Error eliminando aplicación en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# CRUD: DEVICE PROFILES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/device-profiles", response_model=List[DeviceProfileDTO])
async def list_device_profiles(
    tenant_id: Optional[str] = Query(default=None),
    include_vendors: bool = Query(default=True),
    _: Dict = Depends(get_current_user),
) -> List[DeviceProfileDTO]:
    raw_list = await chirpstack_client.fetch_device_profiles(tenant_id, include_vendors=include_vendors)
    tenant_map = {t["tenant_id"]: t["name"] for t in store.tenants}
    return [
        DeviceProfileDTO(
            id=p["id"],
            tenant_id=p["tenant_id"],
            tenant_name=tenant_map.get(p["tenant_id"], p.get("vendor_name") or "Global / Vendor") if p["tenant_id"] else (p.get("vendor_name") or "Global / Vendor"),
            name=p["name"],
            description=p.get("description", ""),
            region=p.get("region", "US915"),
            region_config_id=p.get("region_config_id", ""),
            mac_version=p.get("mac_version", "LORAWAN_1_0_3"),
            reg_params_revision=p.get("reg_params_revision", "A"),
            supports_otaa=p.get("supports_otaa", True),
            supports_class_b=p.get("supports_class_b", False),
            supports_class_c=p.get("supports_class_c", False),
            uplink_interval=int(p.get("uplink_interval", 3600)),
            payload_codec_runtime=p.get("payload_codec_runtime", "NONE"),
            payload_codec_script=p.get("payload_codec_script", ""),
            vendor_id=p.get("vendor_id", ""),
            vendor_name=p.get("vendor_name", ""),
            device_id=p.get("device_id", ""),
            device_name=p.get("device_name", ""),
            firmware_version=p.get("firmware_version", ""),
            is_tenant_profile=bool(p.get("is_tenant_profile", bool(p["tenant_id"]))),
            created_at=p.get("created_at"),
            updated_at=p.get("updated_at"),
        )
        for p in raw_list
    ]


@app.post("/api/device-profiles", response_model=DeviceProfileDTO, status_code=status.HTTP_201_CREATED)
async def create_device_profile(
    req: DeviceProfileCreateRequest,
    _: Dict = Depends(get_current_user),
) -> DeviceProfileDTO:
    try:
        pid = await chirpstack_client.create_device_profile(req.model_dump())
        tenant_map = {t["tenant_id"]: t["name"] for t in store.tenants}
        return DeviceProfileDTO(
            id=pid,
            tenant_id=req.tenant_id,
            tenant_name=tenant_map.get(req.tenant_id, "Tenant"),
            name=req.name,
            description=req.description or "",
            region=req.region,
            region_config_id=req.region_config_id or "",
            mac_version=req.mac_version,
            reg_params_revision=req.reg_params_revision,
            supports_otaa=req.supports_otaa,
            supports_class_b=req.supports_class_b,
            supports_class_c=req.supports_class_c,
            uplink_interval=req.uplink_interval,
            payload_codec_runtime=req.payload_codec_runtime,
            payload_codec_script=req.payload_codec_script or "",
        )
    except Exception as exc:
        logger.error("Error creando perfil de dispositivo en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/device-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device_profile(profile_id: str, _: Dict = Depends(get_current_user)) -> None:
    try:
        await chirpstack_client.delete_device_profile(profile_id)
    except Exception as exc:
        logger.error("Error eliminando perfil de dispositivo en ChirpStack: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# ALERTAS PERSONALIZADAS POR NODO Y GATEWAY
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/nodes/{dev_eui}/alerts", response_model=NodeAlertConfigDTO)
async def get_node_alerts(dev_eui: str, _: Dict = Depends(get_current_user)) -> NodeAlertConfigDTO:
    clean_eui = dev_eui.strip().lower()
    cfg = store.node_alerts.get(clean_eui) or {}
    default_thresh = store.rules.get("NODE_SILENT")
    default_sec = default_thresh.threshold_value if default_thresh else 60
    default_bat = store.rules.get("LOW_BATTERY")
    default_bat_val = default_bat.threshold_value if default_bat else 15

    return NodeAlertConfigDTO(
        dev_eui=clean_eui,
        inactivity_seconds=cfg.get("inactivity_seconds", default_sec),
        inactivity_enabled=cfg.get("inactivity_enabled", True),
        low_battery_threshold=cfg.get("low_battery_threshold", default_bat_val),
        low_battery_enabled=cfg.get("low_battery_enabled", True),
    )


@app.put("/api/nodes/{dev_eui}/alerts", response_model=NodeAlertConfigDTO)
async def update_node_alerts(
    dev_eui: str,
    req: NodeAlertConfigUpdateDTO,
    _: Dict = Depends(get_current_user),
) -> NodeAlertConfigDTO:
    clean_eui = dev_eui.strip().lower()
    current = store.node_alerts.get(clean_eui) or {
        "inactivity_seconds": 60,
        "inactivity_enabled": True,
        "low_battery_threshold": 15,
        "low_battery_enabled": True,
    }
    if req.inactivity_seconds is not None:
        current["inactivity_seconds"] = req.inactivity_seconds
    if req.inactivity_enabled is not None:
        current["inactivity_enabled"] = req.inactivity_enabled
    if req.low_battery_threshold is not None:
        current["low_battery_threshold"] = req.low_battery_threshold
    if req.low_battery_enabled is not None:
        current["low_battery_enabled"] = req.low_battery_enabled

    store.node_alerts[clean_eui] = current
    store._save_node_alerts()
    store.evaluate_all()

    return NodeAlertConfigDTO(
        dev_eui=clean_eui,
        inactivity_seconds=current["inactivity_seconds"],
        inactivity_enabled=current["inactivity_enabled"],
        low_battery_threshold=current["low_battery_threshold"],
        low_battery_enabled=current["low_battery_enabled"],
    )


@app.get("/api/gateways/{gateway_id}/alerts", response_model=GatewayAlertConfigDTO)
async def get_gateway_alerts(gateway_id: str, _: Dict = Depends(get_current_user)) -> GatewayAlertConfigDTO:
    clean_id = gateway_id.strip().lower()
    cfg = store.gw_alerts.get(clean_id) or {}
    default_gw = store.rules.get("GATEWAY_OFFLINE")
    default_sec = (default_gw.threshold_value * 60) if default_gw else 900

    return GatewayAlertConfigDTO(
        gateway_id=clean_id,
        offline_seconds=cfg.get("offline_seconds", default_sec),
        offline_enabled=cfg.get("offline_enabled", True),
    )


@app.put("/api/gateways/{gateway_id}/alerts", response_model=GatewayAlertConfigDTO)
async def update_gateway_alerts(
    gateway_id: str,
    req: GatewayAlertConfigUpdateDTO,
    _: Dict = Depends(get_current_user),
) -> GatewayAlertConfigDTO:
    clean_id = gateway_id.strip().lower()
    current = store.gw_alerts.get(clean_id) or {
        "offline_seconds": 900,
        "offline_enabled": True,
    }
    if req.offline_seconds is not None:
        current["offline_seconds"] = req.offline_seconds
    if req.offline_enabled is not None:
        current["offline_enabled"] = req.offline_enabled

    store.gw_alerts[clean_id] = current
    store._save_gw_alerts()
    store.evaluate_all()

    return GatewayAlertConfigDTO(
        gateway_id=clean_id,
        offline_seconds=current["offline_seconds"],
        offline_enabled=current["offline_enabled"],
    )


@app.get("/api/nodes", response_model=List[NodeMetadataDTO])
async def list_nodes(
    search: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    application_id: Optional[str] = Query(default=None),
    only_with_battery: bool = Query(default=False),
    only_with_alarms: bool = Query(default=False),
    _: Dict = Depends(get_current_user),
) -> List[NodeMetadataDTO]:
    nodes, _ = store.evaluate_all()
    if search:
        q = search.lower()
        nodes = [n for n in nodes if q in n.name.lower() or q in n.dev_eui.lower()]
    if state:
        nodes = [n for n in nodes if n.communication_state == state]
    if tenant_id:
        nodes = [n for n in nodes if n.tenant_id == tenant_id]
    if application_id:
        nodes = [n for n in nodes if n.application_id == application_id]
    if only_with_battery:
        nodes = [n for n in nodes if n.battery_level is not None]
    if only_with_alarms:
        nodes = [n for n in nodes if n.has_active_alarm]
    return nodes


@app.get("/api/nodes/{dev_eui}", response_model=NodeMetadataDTO)
async def get_node_detail(dev_eui: str, _: Dict = Depends(get_current_user)) -> NodeMetadataDTO:
    nodes, _ = store.evaluate_all()
    for node in nodes:
        if node.dev_eui.lower() == dev_eui.lower():
            return node
    raise HTTPException(status_code=404, detail="Nodo no encontrado")


@app.put("/api/nodes/{dev_eui}/location", response_model=NodeMetadataDTO)
async def update_node_location(
    dev_eui: str,
    body: LocationUpdateRequest,
    _: Dict = Depends(get_current_user),
) -> NodeMetadataDTO:
    store.custom_locations[dev_eui] = {"latitude": body.latitude, "longitude": body.longitude}
    store._save_custom_locations()
    for raw in store.raw_nodes:
        if raw["dev_eui"].lower() == dev_eui.lower():
            raw["latitude"] = body.latitude
            raw["longitude"] = body.longitude
            break
    nodes, _ = store.evaluate_all()
    for node in nodes:
        if node.dev_eui.lower() == dev_eui.lower():
            return node
    raise HTTPException(status_code=404, detail="Nodo no encontrado")


@app.get("/api/gateways", response_model=List[GatewayMetadataDTO])
async def list_gateways(
    search: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    _: Dict = Depends(get_current_user),
) -> List[GatewayMetadataDTO]:
    _, gateways = store.evaluate_all()
    if search:
        q = search.lower()
        gateways = [g for g in gateways if q in g.name.lower() or q in g.gateway_id.lower()]
    if state:
        gateways = [g for g in gateways if g.communication_state == state]
    if tenant_id:
        gateways = [g for g in gateways if g.tenant_id == tenant_id]
    return gateways


@app.get("/api/gateways/{gateway_id}", response_model=GatewayMetadataDTO)
async def get_gateway_detail(gateway_id: str, _: Dict = Depends(get_current_user)) -> GatewayMetadataDTO:
    _, gateways = store.evaluate_all()
    for gw in gateways:
        if gw.gateway_id.lower() == gateway_id.lower():
            return gw
    raise HTTPException(status_code=404, detail="Gateway no encontrado")


@app.put("/api/gateways/{gateway_id}/location", response_model=GatewayMetadataDTO)
async def update_gateway_location(
    gateway_id: str,
    body: LocationUpdateRequest,
    _: Dict = Depends(get_current_user),
) -> GatewayMetadataDTO:
    store.custom_locations[gateway_id] = {"latitude": body.latitude, "longitude": body.longitude}
    store._save_custom_locations()
    for raw in store.raw_gateways:
        if raw["gateway_id"].lower() == gateway_id.lower():
            raw["latitude"] = body.latitude
            raw["longitude"] = body.longitude
            break
    _, gateways = store.evaluate_all()
    for gw in gateways:
        if gw.gateway_id.lower() == gateway_id.lower():
            return gw
    raise HTTPException(status_code=404, detail="Gateway no encontrado")


@app.get("/api/tenants", response_model=List[TenantDTO])
async def list_tenants(_: Dict = Depends(get_current_user)) -> List[TenantDTO]:
    nodes, gateways = store.evaluate_all()
    result: List[TenantDTO] = []
    for t in store.tenants:
        tid = t["tenant_id"]
        result.append(
            TenantDTO(
                tenant_id=tid,
                name=t["name"],
                applications_count=sum(1 for a in store.applications if a["tenant_id"] == tid),
                nodes_count=sum(1 for n in nodes if n.tenant_id == tid),
                gateways_count=sum(1 for g in gateways if g.tenant_id == tid),
            )
        )
    return result


@app.get("/api/applications", response_model=List[ApplicationDTO])
async def list_applications(
    tenant_id: Optional[str] = Query(default=None), _: Dict = Depends(get_current_user)
) -> List[ApplicationDTO]:
    nodes, _ = store.evaluate_all()
    apps = store.applications
    if tenant_id:
        apps = [a for a in apps if a["tenant_id"] == tenant_id]
    return [
        ApplicationDTO(
            application_id=a["application_id"],
            tenant_id=a["tenant_id"],
            tenant_name=a["tenant_name"],
            name=a["name"],
            nodes_count=sum(1 for n in nodes if n.application_id == a["application_id"]),
            active_nodes_count=sum(
                1
                for n in nodes
                if n.application_id == a["application_id"] and n.communication_state == "ACTIVE"
            ),
        )
        for a in apps
    ]


@app.get("/api/alarms", response_model=List[AlarmDTO])
async def list_alarms(
    status_filter: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    _: Dict = Depends(get_current_user),
) -> List[AlarmDTO]:
    store.evaluate_all()
    now = datetime.now(timezone.utc)
    active_visible = [
        a
        for a in alarm_engine.active_alarms.values()
        if not alarm_engine.is_silenced(a.rule_code, a.entity_type, a.entity_id, now)
    ]
    all_alarms = active_visible + alarm_engine.resolved_alarms
    effective_status = (status_filter or status or "").upper()
    if effective_status:
        all_alarms = [a for a in all_alarms if a.status.upper() == effective_status]
    if severity:
        all_alarms = [a for a in all_alarms if a.severity.upper() == severity.upper()]
    return all_alarms


@app.post("/api/alarms/{alarm_id}/ack", response_model=AlarmDTO)
@app.post("/api/alarms/{alarm_id}/acknowledge", response_model=AlarmDTO)
async def acknowledge_alarm(alarm_id: str, user: Dict = Depends(get_current_user)) -> AlarmDTO:
    alarm = alarm_engine.acknowledge(alarm_id, user["full_name"])
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarma activa no encontrada")
    return alarm


@app.post("/api/alarms/{alarm_id}/resolve", response_model=AlarmDTO)
async def resolve_alarm(alarm_id: str, user: Dict = Depends(get_current_user)) -> AlarmDTO:
    alarm = alarm_engine.manual_resolve(alarm_id, user["full_name"])
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarma no encontrada")
    return alarm


@app.get("/api/history/events", response_model=List[OperationalEventDTO])
async def list_operational_events(
    entity_id: Optional[str] = Query(default=None), _: Dict = Depends(get_current_user)
) -> List[OperationalEventDTO]:
    events = alarm_engine.events
    if entity_id:
        events = [e for e in events if e.entity_id == entity_id]
    return events


@app.get("/api/preferences/notifications", response_model=NotificationPreferencesDTO)
async def get_notification_preferences(user: Dict = Depends(get_current_user)) -> NotificationPreferencesDTO:
    return store.get_user_prefs(user["username"])


@app.put("/api/preferences/notifications", response_model=NotificationPreferencesDTO)
async def update_notification_preferences(
    prefs: NotificationPreferencesDTO, user: Dict = Depends(get_current_user)
) -> NotificationPreferencesDTO:
    store.user_prefs[user["username"]] = prefs
    return prefs


@app.get("/api/rules", response_model=List[AlarmRuleUpdateDTO])
async def get_rules(_: Dict = Depends(get_current_user)) -> List[AlarmRuleUpdateDTO]:
    return list(store.rules.values())


@app.put("/api/rules/{code}", response_model=AlarmRuleUpdateDTO)
async def update_rule(
    code: str, rule: AlarmRuleUpdateDTO, _: Dict = Depends(get_current_user)
) -> AlarmRuleUpdateDTO:
    store.rules[code] = rule
    store._save_rules()
    store.evaluate_all()
    return rule


# ---------------------------------------------------------------------------
# Push token — registro y eliminación de dispositivos móviles
# ---------------------------------------------------------------------------

class PushTokenRequest(BaseModel):
    """Cuerpo esperado por POST /api/push-token."""
    token: str
    platform: Literal["android", "ios"]


@app.post("/api/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    body: PushTokenRequest,
    user: Dict = Depends(get_current_user),
) -> None:
    """
    Registra o actualiza el token de notificación push del dispositivo actual.
    Si el token ya existe para el usuario no se duplica.
    Un mismo usuario puede tener varios tokens (múltiples dispositivos).
    """
    username = user["username"]
    if username not in store.push_tokens:
        store.push_tokens[username] = []
    tokens = store.push_tokens[username]
    if body.token not in tokens:
        tokens.append(body.token)


@app.delete("/api/push-token", status_code=status.HTTP_204_NO_CONTENT)
async def remove_push_token(
    body: PushTokenRequest,
    user: Dict = Depends(get_current_user),
) -> None:
    """
    Elimina el token de notificación push del usuario actual.
    Si el token no existe para el usuario, la operación es silenciosa (idempotente).
    """
    username = user["username"]
    tokens = store.push_tokens.get(username, [])
    if body.token in tokens:
        tokens.remove(body.token)


@app.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket, token: Optional[str] = Query(default=None)) -> None:
    if token:
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            if not payload.get("sub") or payload.get("sub") not in USERS_DB:
                await websocket.close(code=1008)
                return
        except JWTError:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    store.ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in store.ws_clients:
            store.ws_clients.remove(websocket)