from datetime import datetime
from typing import List, Literal, Optional
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


class TenantDTO(BaseModel):
    tenant_id: str
    name: str
    applications_count: int
    nodes_count: int
    gateways_count: int


class ApplicationDTO(BaseModel):
    application_id: str
    tenant_id: str
    tenant_name: str
    name: str
    nodes_count: int
    active_nodes_count: int


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
    detected_at: datetime
    age_minutes: int
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


class AlarmRuleUpdateDTO(BaseModel):
    code: str
    severity: AlarmSeverity
    threshold_value: int
    tolerance_minutes: int
    is_enabled: bool