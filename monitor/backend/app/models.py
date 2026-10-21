"""
Modelos ORM SQLAlchemy 2.x para todas las tablas del esquema operativo LoRaWAN Monitor.
IMPORTANTE: No contiene ningún campo de telemetría de sensores
            (temperatura, humedad, RSSI, SNR, CO2, presión, payload, etc.).
            Solo se modelan metadatos operativos y de conectividad.

Tablas implementadas (en orden de dependencias FK):
  1. User                        → users
  2. UserPushToken               → user_push_tokens
  3. UserNotificationPreference  → user_notification_preferences
  4. AlarmRule                   → alarm_rules
  5. CachedTenant                → cached_tenants
  6. CachedApplication           → cached_applications
  7. CachedNode                  → cached_nodes
  8. CachedGateway               → cached_gateways
  9. Alarm                       → alarms
  10. EntityStateEvent           → entity_state_events
  11. NotificationLog            → notification_logs
"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------------------------------------------------------------------------
# 1. Users — Usuarios de la plataforma móvil
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Operador de Red"
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Vacío = acceso global según permisos del token ChirpStack
    allowed_tenant_ids: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    push_tokens: Mapped[List["UserPushToken"]] = relationship(
        "UserPushToken", back_populates="user", cascade="all, delete-orphan"
    )
    notification_preference: Mapped[Optional["UserNotificationPreference"]] = relationship(
        "UserNotificationPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    notification_logs: Mapped[List["NotificationLog"]] = relationship(
        "NotificationLog", back_populates="user", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 2. UserPushToken — Dispositivos móviles registrados para Push (FCM / APNs)
# ---------------------------------------------------------------------------
class UserPushToken(Base):
    __tablename__ = "user_push_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Plataforma del dispositivo: 'android' o 'ios'
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    push_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    user: Mapped["User"] = relationship("User", back_populates="push_tokens")


# ---------------------------------------------------------------------------
# 3. UserNotificationPreference — Preferencias de notificación por usuario
# ---------------------------------------------------------------------------
class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_high: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_medium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_low: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_node_silent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_gateway_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_low_battery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_chirpstack_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_recovery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    user: Mapped["User"] = relationship(
        "User", back_populates="notification_preference"
    )


# ---------------------------------------------------------------------------
# 4. AlarmRule — Reglas configurables de alarmas operativas
#    (Solo metadatos/conectividad: silencio de nodo, gateway offline,
#     batería baja, desconexión ChirpStack, error de integración)
# ---------------------------------------------------------------------------
class AlarmRule(Base):
    __tablename__ = "alarm_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Código único de la regla. Ver CHECK constraint en SQL.
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Severidad: CRITICAL | HIGH | MEDIUM | LOW
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # Minutos sin comunicación o porcentaje de batería según el tipo de regla
    threshold_value: Mapped[int] = mapped_column(Integer, nullable=False)
    # Tolerancia en minutos antes de disparar la alarma
    tolerance_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    auto_recover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# 5. CachedTenant — Caché de metadatos de Tenants ChirpStack
# ---------------------------------------------------------------------------
class CachedTenant(Base):
    __tablename__ = "cached_tenants"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    applications: Mapped[List["CachedApplication"]] = relationship(
        "CachedApplication", back_populates="tenant", cascade="all, delete-orphan"
    )
    nodes: Mapped[List["CachedNode"]] = relationship(
        "CachedNode", back_populates="tenant", cascade="all, delete-orphan"
    )
    gateways: Mapped[List["CachedGateway"]] = relationship(
        "CachedGateway", back_populates="tenant"
    )


# ---------------------------------------------------------------------------
# 6. CachedApplication — Caché de metadatos de Aplicaciones ChirpStack
# ---------------------------------------------------------------------------
class CachedApplication(Base):
    __tablename__ = "cached_applications"

    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("cached_tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    tenant: Mapped["CachedTenant"] = relationship(
        "CachedTenant", back_populates="applications"
    )
    nodes: Mapped[List["CachedNode"]] = relationship(
        "CachedNode", back_populates="application", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 7. CachedNode — Caché de metadatos operativos de nodos LoRaWAN
#    SIN campos de sensores: no hay temperatura, humedad, CO2, presión,
#    RSSI, SNR, payload ni ningún dato de telemetría de sensores.
# ---------------------------------------------------------------------------
class CachedNode(Base):
    __tablename__ = "cached_nodes"

    dev_eui: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("cached_applications.application_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("cached_tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    # Estado de comunicación: ACTIVE | NO_COMMUNICATION | UNKNOWN
    communication_state: Mapped[str] = mapped_column(String(24), nullable=False)
    state_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Nivel de batería en porcentaje. NULL = No disponible en este nodo.
    battery_level: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    # TRUE = alimentado por red externa; FALSE = batería; NULL = desconocido
    external_power: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Coordenadas GPS. NULL = No disponibles en este nodo.
    latitude: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    application: Mapped["CachedApplication"] = relationship(
        "CachedApplication", back_populates="nodes"
    )
    tenant: Mapped["CachedTenant"] = relationship(
        "CachedTenant", back_populates="nodes"
    )


# ---------------------------------------------------------------------------
# 8. CachedGateway — Caché de metadatos operativos de gateways LoRaWAN
# ---------------------------------------------------------------------------
class CachedGateway(Base):
    __tablename__ = "cached_gateways"

    gateway_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("cached_tenants.tenant_id", ondelete="SET NULL"),
        nullable=True,
    )
    # Estado de comunicación: ACTIVE | NO_COMMUNICATION | UNKNOWN
    communication_state: Mapped[str] = mapped_column(String(24), nullable=False)
    state_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Coordenadas GPS del gateway. NULL = No disponibles.
    latitude: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    tenant: Mapped[Optional["CachedTenant"]] = relationship(
        "CachedTenant", back_populates="gateways"
    )


# ---------------------------------------------------------------------------
# 9. Alarm — Alarmas operativas con deduplicación activa
#    Índice único parcial en SQL: (rule_code, entity_type, entity_id)
#    WHERE status IN ('ACTIVE', 'ACKNOWLEDGED')
# ---------------------------------------------------------------------------
class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # FK a alarm_rules.code para trazabilidad de la regla que disparó la alarma
    rule_code: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("alarm_rules.code"),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # Estado: ACTIVE | ACKNOWLEDGED | RESOLVED
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Tipo de entidad afectada: NODE | GATEWAY | INFRASTRUCTURE
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(150), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    condition_description: Mapped[str] = mapped_column(Text, nullable=False)
    # Valor observado que activó la alarma (ej. "Sin actividad durante 45 min")
    observed_value: Mapped[str] = mapped_column(String(80), nullable=False)
    # Umbral formateado para presentación (ej. "30 min" o "15%")
    threshold_formatted: Mapped[str] = mapped_column(String(80), nullable=False)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relaciones
    notification_logs: Mapped[List["NotificationLog"]] = relationship(
        "NotificationLog", back_populates="alarm", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 10. EntityStateEvent — Historial de auditoría de alarmas y cambios de estado
# ---------------------------------------------------------------------------
class EntityStateEvent(Base):
    __tablename__ = "entity_state_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Tipo de entidad: NODE | GATEWAY | INFRASTRUCTURE
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(150), nullable=False)
    # Tipo de evento: STATE_CHANGE, ALARM_TRIGGERED, ALARM_ACK, ALARM_RESOLVED, BATTERY_WARN
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_state: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    new_state: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# 11. NotificationLog — Registro de envíos y errores de notificaciones Push
#     Constraint UNIQUE (alarm_id, user_id) evita reenvío duplicado.
# ---------------------------------------------------------------------------
class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        # Un registro de envío push por alarma+usuario para evitar duplicados
        UniqueConstraint("alarm_id", "user_id", name="uq_notification_alarm_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alarm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alarms.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    push_token: Mapped[str] = mapped_column(Text, nullable=False)
    # Estado de entrega: SENT | FAILED | SKIPPED_DEDUP
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relaciones
    alarm: Mapped["Alarm"] = relationship("Alarm", back_populates="notification_logs")
    user: Mapped["User"] = relationship("User", back_populates="notification_logs")
