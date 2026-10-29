-- ============================================================================
-- MIGRACIÓN 001: ESQUEMA OPERATIVO LORAWAN MONITOR (SIN DATOS DE SENSORES)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Usuarios de la plataforma móvil
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(80) UNIQUE NOT NULL,
    full_name VARCHAR(120) NOT NULL,
    role VARCHAR(60) NOT NULL DEFAULT 'Operador de Red',
    password_hash TEXT NOT NULL,
    allowed_tenant_ids TEXT[] DEFAULT '{}', -- Vacío = acceso global según permisos
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Dispositivos móviles registrados para Push (FCM / APNs)
CREATE TABLE user_push_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(16) NOT NULL CHECK (platform IN ('android', 'ios')),
    push_token TEXT UNIQUE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Preferencias de notificación por usuario
CREATE TABLE user_notification_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    push_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notify_critical BOOLEAN NOT NULL DEFAULT TRUE,
    notify_high BOOLEAN NOT NULL DEFAULT TRUE,
    notify_medium BOOLEAN NOT NULL DEFAULT TRUE,
    notify_low BOOLEAN NOT NULL DEFAULT FALSE,
    notify_node_silent BOOLEAN NOT NULL DEFAULT TRUE,
    notify_gateway_offline BOOLEAN NOT NULL DEFAULT TRUE,
    notify_low_battery BOOLEAN NOT NULL DEFAULT TRUE,
    notify_chirpstack_loss BOOLEAN NOT NULL DEFAULT TRUE,
    notify_recovery BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Reglas configurables de alarmas operativas (Exclusivamente metadatos/conectividad)
CREATE TABLE alarm_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(50) UNIQUE NOT NULL CHECK (
        code IN (
            'NODE_SILENT',
            'GATEWAY_OFFLINE',
            'LOW_BATTERY',
            'CHIRPSTACK_DISCONNECTED',
            'INTEGRATION_ERROR'
        )
    ),
    name VARCHAR(120) NOT NULL,
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')),
    threshold_value INTEGER NOT NULL, -- Minutos sin comunicación o % de batería
    tolerance_minutes INTEGER NOT NULL DEFAULT 5,
    auto_recover BOOLEAN NOT NULL DEFAULT TRUE,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Caché de metadatos de Tenants, Aplicaciones, Nodos y Gateways
CREATE TABLE cached_tenants (
    tenant_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE cached_applications (
    application_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES cached_tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(150) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE cached_nodes (
    dev_eui VARCHAR(32) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    application_id VARCHAR(64) NOT NULL REFERENCES cached_applications(application_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES cached_tenants(tenant_id) ON DELETE CASCADE,
    communication_state VARCHAR(24) NOT NULL CHECK (communication_state IN ('ACTIVE', 'NO_COMMUNICATION', 'UNKNOWN')),
    state_criteria TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NULL,
    battery_level NUMERIC(5,2) NULL, -- NULL = No disponible
    external_power BOOLEAN NULL,
    latitude DOUBLE PRECISION NULL,  -- NULL = No disponible
    longitude DOUBLE PRECISION NULL, -- NULL = No disponible
    registered_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE cached_gateways (
    gateway_id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    tenant_id VARCHAR(64) NULL REFERENCES cached_tenants(tenant_id) ON DELETE SET NULL,
    communication_state VARCHAR(24) NOT NULL CHECK (communication_state IN ('ACTIVE', 'NO_COMMUNICATION', 'UNKNOWN')),
    state_criteria TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NULL,
    latitude DOUBLE PRECISION NULL,
    longitude DOUBLE PRECISION NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Alarmas operativas con deduplicación activa
CREATE TABLE alarms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_code VARCHAR(50) NOT NULL REFERENCES alarm_rules(code),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')),
    status VARCHAR(16) NOT NULL CHECK (status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED')),
    entity_type VARCHAR(20) NOT NULL CHECK (entity_type IN ('NODE', 'GATEWAY', 'INFRASTRUCTURE')),
    entity_id VARCHAR(64) NOT NULL,
    entity_name VARCHAR(150) NOT NULL,
    title VARCHAR(180) NOT NULL,
    condition_description TEXT NOT NULL,
    observed_value VARCHAR(80) NOT NULL,
    threshold_formatted VARCHAR(80) NOT NULL,
    last_activity_at TIMESTAMPTZ NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ NULL,
    acknowledged_by VARCHAR(80) NULL,
    resolved_at TIMESTAMPTZ NULL
);

-- Índice único parcial para evitar duplicados de la misma incidencia mientras no esté resuelta
CREATE UNIQUE INDEX idx_alarms_dedup_active
ON alarms (rule_code, entity_type, entity_id)
WHERE status IN ('ACTIVE', 'ACKNOWLEDGED');

-- 7. Historial de auditoría de alarmas y cambios de estado operativo
CREATE TABLE entity_state_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type VARCHAR(20) NOT NULL CHECK (entity_type IN ('NODE', 'GATEWAY', 'INFRASTRUCTURE')),
    entity_id VARCHAR(64) NOT NULL,
    entity_name VARCHAR(150) NOT NULL,
    event_type VARCHAR(50) NOT NULL, -- STATE_CHANGE, ALARM_TRIGGERED, ALARM_ACK, ALARM_RESOLVED, BATTERY_WARN
    previous_state VARCHAR(60) NULL,
    new_state VARCHAR(60) NOT NULL,
    detail TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Registro de envíos y errores de notificaciones Push
CREATE TABLE notification_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alarm_id UUID NOT NULL REFERENCES alarms(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    push_token TEXT NOT NULL,
    delivery_status VARCHAR(20) NOT NULL CHECK (delivery_status IN ('SENT', 'FAILED', 'SKIPPED_DEDUP')),
    error_message TEXT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (alarm_id, user_id) -- Evita reenviar push repetido para la misma alarma y usuario
);

-- Reglas operativas iniciales por defecto
INSERT INTO alarm_rules (code, name, severity, threshold_value, tolerance_minutes, auto_recover) VALUES
('NODE_SILENT', 'Nodo sin comunicación', 'CRITICAL', 30, 5, TRUE),
('GATEWAY_OFFLINE', 'Gateway sin comunicación', 'HIGH', 15, 2, TRUE),
('LOW_BATTERY', 'Batería en nivel crítico', 'HIGH', 15, 0, TRUE),
('CHIRPSTACK_DISCONNECTED', 'Pérdida de conexión con ChirpStack', 'CRITICAL', 1, 0, TRUE),
('INTEGRATION_ERROR', 'Error técnico de integración ChirpStack', 'MEDIUM', 1, 0, TRUE)
ON CONFLICT (code) DO NOTHING;


-- Usuario administrador inicial con hash bcrypt (contraseña: demopassword)
INSERT INTO users (username, full_name, role, password_hash, is_active) VALUES
('operador.demo', 'Operador de Red', 'Administrador LoRaWAN', '$2b$12$K8yR2u/fR.hA7/rFqCqBde6x5Y3P7qRzWk0eIeP.0c8WzYv7rE1a.', TRUE)
ON CONFLICT (username) DO NOTHING;
