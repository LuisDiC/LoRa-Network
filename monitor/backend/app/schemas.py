from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

CommState = Literal["ACTIVE", "NO_COMMUNICATION", "UNKNOWN"]
AlarmSeverity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
AlarmStatus = Literal["ACTIVE", "ACKNOWLEDGED", "RESOLVED"]
EntityType = Literal["NODE", "GATEWAY", "INFRASTRUCTURE"]


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_full_name: str
    role: str
    demo_mode: bool


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=4)
    full_name: str = Field(..., min_length=2, max_length=120)
    role: str = Field(default="Operador de Red", max_length=60)


class UserResponseDTO(BaseModel):
    username: str
    full_name: str
    role: str
    created_at: Optional[str] = None


class UserPasswordChangeRequest(BaseModel):
    new_password: str = Field(..., min_length=4)


# ---------------------------------------------------------------------------
# TENANTS (ORGANIZACIONES)
# ---------------------------------------------------------------------------

class TenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    can_have_gateways: bool = True
    max_device_count: int = 0
    max_gateway_count: int = 0


class TenantUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    can_have_gateways: bool = True
    max_device_count: int = 0
    max_gateway_count: int = 0


class TenantDTO(BaseModel):
    tenant_id: str
    name: str
    description: Optional[str] = ""
    can_have_gateways: bool = True
    max_device_count: int = 0
    max_gateway_count: int = 0
    applications_count: int = 0
    nodes_count: int = 0
    gateways_count: int = 0


# ---------------------------------------------------------------------------
# APLICACIONES
# ---------------------------------------------------------------------------

class ApplicationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    tenant_id: str


class ApplicationUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""


class ApplicationDTO(BaseModel):
    application_id: str
    tenant_id: str
    tenant_name: str
    name: str
    description: Optional[str] = ""
    nodes_count: int = 0
    active_nodes_count: int = 0


# ---------------------------------------------------------------------------
# DEVICE PROFILES (PERFILES DE DISPOSITIVO)
# ---------------------------------------------------------------------------

class DeviceProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    tenant_id: str
    description: Optional[str] = ""
    region: str = "US915"
    region_config_id: Optional[str] = ""
    mac_version: str = "LORAWAN_1_0_3"
    reg_params_revision: str = "A"
    adr_algorithm_id: str = "default"
    supports_otaa: bool = True
    supports_class_b: bool = False
    supports_class_c: bool = False
    uplink_interval: int = Field(default=3600, ge=1)
    payload_codec_runtime: str = "NONE"
    payload_codec_script: Optional[str] = ""


class DeviceProfileUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    region: str = "US915"
    region_config_id: Optional[str] = ""
    mac_version: str = "LORAWAN_1_0_3"
    reg_params_revision: str = "A"
    adr_algorithm_id: str = "default"
    supports_otaa: bool = True
    supports_class_b: bool = False
    supports_class_c: bool = False
    uplink_interval: int = Field(default=3600, ge=1)
    payload_codec_runtime: str = "NONE"
    payload_codec_script: Optional[str] = ""


class DeviceProfileDTO(BaseModel):
    id: str
    tenant_id: str
    tenant_name: Optional[str] = "No asignado"
    name: str
    description: Optional[str] = ""
    region: str = "US915"
    region_config_id: Optional[str] = ""
    mac_version: str = "LORAWAN_1_0_3"
    reg_params_revision: str = "A"
    supports_otaa: bool = True
    supports_class_b: bool = False
    supports_class_c: bool = False
    uplink_interval: int = 3600
    payload_codec_runtime: str = "NONE"
    payload_codec_script: Optional[str] = ""
    vendor_id: Optional[str] = ""
    vendor_name: Optional[str] = ""
    device_id: Optional[str] = ""
    device_name: Optional[str] = ""
    firmware_version: Optional[str] = ""
    is_tenant_profile: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GatewayCreateRequest(BaseModel):
    gateway_id: str = Field(..., min_length=16, max_length=16, description="Gateway EUI de 16 caracteres hex")
    name: str = Field(..., min_length=1, max_length=100)
    tenant_id: str
    description: Optional[str] = ""
    stats_interval: int = 30
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = 0.0


class GatewayUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    stats_interval: int = 30
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = 0.0


class DeviceCreateRequest(BaseModel):
    dev_eui: str = Field(..., min_length=16, max_length=16, description="DevEUI de 16 caracteres hex")
    name: str = Field(..., min_length=1, max_length=100)
    application_id: str
    device_profile_id: str
    description: Optional[str] = ""
    app_key: Optional[str] = Field(None, min_length=32, max_length=32, description="AppKey OTAA de 32 caracteres hex")
    is_disabled: bool = False
    skip_fcnt_check: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class DeviceUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = ""
    device_profile_id: Optional[str] = None
    is_disabled: bool = False
    skip_fcnt_check: bool = False
    app_key: Optional[str] = Field(None, description="AppKey OTAA opcional de 32 caracteres hex para rotar clave")
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class NodeMetadataDTO(BaseModel):
    dev_eui: str
    name: str
    application_id: str
    application_name: str
    tenant_id: str
    tenant_name: str
    communication_state: CommState
    state_label: str = Field(description="Activo, Sin comunicación o Estado desconocido")
    state_criteria: str = Field(description="Criterio explícito aplicado sobre lastSeenAt")
    last_seen_at: Optional[datetime] = None
    minutes_since_last_seen: Optional[int] = None
    seconds_since_last_seen: Optional[int] = None
    battery_level: Optional[float] = Field(default=None, description="None si no está disponible")
    battery_display: str = Field(default="No disponible")
    location_available: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_display: str = Field(default="No disponible")
    registered_at: Optional[datetime] = None
    registered_display: str = Field(default="No disponible")
    has_active_alarm: bool = False
    highest_alarm_severity: Optional[AlarmSeverity] = None
    # Métricas de calidad de enlace RF (dBm / dB)
    rssi: Optional[int] = Field(default=None, description="RSSI del último uplink en dBm")
    snr: Optional[float] = Field(default=None, description="SNR del último uplink en dB")
    rssi_display: str = Field(default="— dBm")
    snr_display: str = Field(default="— dB")
    signal_quality: str = Field(default="Sin datos", description="Excelente, Buena, Regular, Débil o Sin datos")
    dr: Optional[int] = None
    spreading_factor: Optional[int] = None
    bandwidth_khz: Optional[int] = None
    frequency_mhz: Optional[float] = None
    f_cnt: Optional[int] = None
    f_port: Optional[int] = None
    dev_addr: Optional[str] = None
    adr: Optional[bool] = None
    last_gateway_id: Optional[str] = None
    rx_gateways: List[Dict[str, Any]] = Field(default_factory=list)


class ChirpStackEventLogDTO(BaseModel):
    id: str
    time: datetime
    event_type: str = Field(description="up, join, status, ack, txack, log, down, location")
    dev_eui: str
    device_name: str = ""
    application_id: Optional[str] = ""
    application_name: Optional[str] = ""
    dev_addr: Optional[str] = None
    f_cnt: Optional[int] = None
    f_port: Optional[int] = None
    dr: Optional[int] = None
    adr: Optional[bool] = None
    confirmed: Optional[bool] = None
    rssi: Optional[int] = None
    snr: Optional[float] = None
    frequency_mhz: Optional[float] = None
    spreading_factor: Optional[int] = None
    bandwidth_khz: Optional[int] = None
    gateway_id: Optional[str] = None
    data_base64: Optional[str] = None
    data_hex: Optional[str] = None
    decoded_object: Optional[Dict[str, Any]] = None
    log_level: Optional[str] = None
    log_code: Optional[str] = None
    log_description: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class LoRaWANFrameLogDTO(BaseModel):
    id: str
    time: datetime
    direction: Literal["UPLINK", "DOWNLINK"] = "UPLINK"
    m_type: str = Field(description="UnconfirmedDataUp, ConfirmedDataUp, JoinRequest, JoinAccept, UnconfirmedDataDown, ConfirmedDataDown")
    dev_eui: Optional[str] = None
    device_name: Optional[str] = None
    dev_addr: Optional[str] = None
    gateway_id: Optional[str] = None
    rx_gateways: List[Dict[str, Any]] = Field(default_factory=list)
    rssi: Optional[int] = None
    snr: Optional[float] = None
    frequency_mhz: Optional[float] = None
    spreading_factor: Optional[int] = None
    bandwidth_khz: Optional[int] = None
    code_rate: Optional[str] = None
    dr: Optional[int] = None
    channel: Optional[int] = None
    adr: Optional[bool] = None
    f_cnt: Optional[int] = None
    f_port: Optional[int] = None
    confirmed: Optional[bool] = None
    phy_payload_hex: Optional[str] = None
    data_hex: Optional[str] = None
    data_base64: Optional[str] = None
    mic_hex: Optional[str] = None
    tx_info: Dict[str, Any] = Field(default_factory=dict)
    rx_info: List[Dict[str, Any]] = Field(default_factory=list)
    frame_details: Dict[str, Any] = Field(default_factory=dict)


class GatewayMetadataDTO(BaseModel):
    gateway_id: str
    name: str
    tenant_id: Optional[str] = None
    tenant_name: str = "No disponible"
    communication_state: CommState
    state_label: str
    state_criteria: str
    last_seen_at: Optional[datetime] = None
    minutes_since_last_seen: Optional[int] = None
    seconds_since_last_seen: Optional[int] = None
    location_available: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_display: str = "No disponible"
    has_active_alarm: bool = False


class LocationUpdateRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class AlarmDTO(BaseModel):
    id: str
    rule_code: str
    severity: AlarmSeverity
    status: AlarmStatus
    entity_type: EntityType
    entity_id: str
    entity_name: str
    title: str
    condition_description: str
    observed_value: str
    threshold_formatted: str
    last_activity_at: Optional[datetime] = None
    last_activity_timestamp: Optional[str] = None
    detected_at: datetime
    age_minutes: int
    age_seconds: Optional[int] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[datetime] = None


class OperationalEventDTO(BaseModel):
    id: str
    entity_type: EntityType
    entity_id: str
    entity_name: str
    event_type: str
    previous_state: Optional[str] = None
    new_state: str
    detail: str
    occurred_at: datetime


class DashboardSummaryDTO(BaseModel):
    chirpstack_connected: bool
    chirpstack_status_detail: str
    last_sync_at: datetime
    is_stale: bool
    demo_mode: bool

    total_nodes: int
    active_nodes: int
    silent_nodes: int
    unknown_nodes: int
    low_battery_nodes: int

    total_gateways: int
    active_gateways: int
    inactive_gateways: int

    accessible_tenants: int
    accessible_applications: int

    alarms_total_active: int
    alarms_critical: int
    alarms_high: int
    alarms_medium: int
    alarms_low: int

    highest_priority_alarm: Optional[AlarmDTO] = None
    recent_events: List[OperationalEventDTO] = []


class NotificationPreferencesDTO(BaseModel):
    push_enabled: bool
    notify_critical: bool
    notify_high: bool
    notify_medium: bool
    notify_low: bool
    notify_node_silent: bool
    notify_gateway_offline: bool
    notify_low_battery: bool
    notify_chirpstack_loss: bool
    notify_recovery: bool


class NodeAlertConfigDTO(BaseModel):
    dev_eui: str
    inactivity_seconds: int = Field(60, ge=5, le=86400, description="Tiempo en segundos para alertar inactividad")
    inactivity_enabled: bool = True
    low_battery_threshold: int = Field(15, ge=1, le=99, description="Porcentaje de batería para alertar")
    low_battery_enabled: bool = True


class NodeAlertConfigUpdateDTO(BaseModel):
    inactivity_seconds: Optional[int] = Field(None, ge=5, le=86400)
    inactivity_enabled: Optional[bool] = None
    low_battery_threshold: Optional[int] = Field(None, ge=1, le=99)
    low_battery_enabled: Optional[bool] = None


class GatewayAlertConfigDTO(BaseModel):
    gateway_id: str
    offline_seconds: int = Field(900, ge=10, le=86400, description="Tiempo en segundos para alertar desconexión")
    offline_enabled: bool = True


class GatewayAlertConfigUpdateDTO(BaseModel):
    offline_seconds: Optional[int] = Field(None, ge=10, le=86400)
    offline_enabled: Optional[bool] = None


class AlarmRuleUpdateDTO(BaseModel):
    code: str
    severity: AlarmSeverity
    threshold_value: int
    unit: str = "seconds"
    tolerance_minutes: int = 0
    is_enabled: bool
