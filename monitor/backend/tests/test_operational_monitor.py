from datetime import datetime, timedelta, timezone
import json
import pytest
from app.services.chirpstack_client import (
    evaluate_communication_state,
    format_battery,
    format_location,
    sanitize_mqtt_event,
)
from app.services.alarm_engine import OperationalAlarmEngine
from app.services.push_service import PushNotificationService
from app.schemas import NotificationPreferencesDTO


def test_communication_states_distinction():
    now = datetime.now(timezone.utc)

    # 1. Dispositivo registrado que nunca ha transmitido
    state_unk, label_unk, _, mins_unk = evaluate_communication_state(None, 30, now)
    assert state_unk == "UNKNOWN"
    assert label_unk == "Estado desconocido"
    assert mins_unk is None

    # 2. Dispositivo con transmisión reciente (dentro del umbral)
    state_act, _, criteria_act, mins_act = evaluate_communication_state(
        now - timedelta(minutes=10), 30, now
    )
    assert state_act == "ACTIVE"
    assert mins_act == 10
    assert "≤ 30 min" in criteria_act

    # 3. Dispositivo sin comunicación (supera umbral)
    state_sil, _, criteria_sil, mins_sil = evaluate_communication_state(
        now - timedelta(minutes=45), 30, now
    )
    assert state_sil == "NO_COMMUNICATION"
    assert mins_sil == 45
    assert "Inferido por inactividad" in criteria_sil


def test_strict_purging_of_sensor_payloads_in_mqtt():
    raw_mqtt = json.dumps(
        {
            "deviceInfo": {
                "devEui": "A84041B8D2517C21",
                "deviceName": "Nodo Cámara 02",
                "tenantId": "tenant-1",
                "applicationId": "app-1",
            },
            "time": "2026-10-16T10:00:00Z",
            "data": "AQIDBA==",
            "object": {"temperature": 24.5, "humidity": 65.2, "co2": 410},
        }
    ).encode("utf-8")

    sanitized = sanitize_mqtt_event(
        "application/app-1/device/A84041B8D2517C21/event/up", raw_mqtt
    )
    assert sanitized is not None
    assert sanitized["dev_eui"] == "A84041B8D2517C21"
    assert "data" not in sanitized
    assert "object" not in sanitized
    assert "temperature" not in str(sanitized)


def test_alarm_deduplication_and_state_lifecycle():
    engine = OperationalAlarmEngine()
    now = datetime.now(timezone.utc)

    # Primera detección -> crea alarma nueva
    alarm1, is_new1 = engine.trigger_or_update(
        rule_code="NODE_SILENT",
        severity="CRITICAL",
        entity_type="NODE",
        entity_id="A84041B8D2517C21",
        entity_name="Nodo Cámara 02",
        title="Nodo sin comunicación",
        condition_description="Sin actividad",
        observed_value="35 min",
        threshold_formatted="30 min",
        last_activity_at=now - timedelta(minutes=35),
        now=now,
    )
    assert is_new1 is True
    assert alarm1.status == "ACTIVE"

    # Segunda evaluación del mismo fallo -> NO duplica
    alarm2, is_new2 = engine.trigger_or_update(
        rule_code="NODE_SILENT",
        severity="CRITICAL",
        entity_type="NODE",
        entity_id="A84041B8D2517C21",
        entity_name="Nodo Cámara 02",
        title="Nodo sin comunicación",
        condition_description="Sin actividad",
        observed_value="42 min",
        threshold_formatted="30 min",
        last_activity_at=now - timedelta(minutes=42),
        now=now + timedelta(minutes=7),
    )
    assert is_new2 is False
    assert alarm1.id == alarm2.id
    assert len(engine.active_alarms) == 1

    # Reconocimiento (ACK)
    acked = engine.acknowledge(alarm1.id, "Operador Demo")
    assert acked is not None
    assert acked.status == "ACKNOWLEDGED"

    # Recuperación -> pasa a RESOLVED
    resolved = engine.resolve_if_recovered(
        "NODE_SILENT", "NODE", "A84041B8D2517C21", "Uplink recibido"
    )
    assert resolved is not None
    assert resolved.status == "RESOLVED"
    assert len(engine.active_alarms) == 0


@pytest.mark.asyncio
async def test_push_notification_deduplication():
    engine = OperationalAlarmEngine()
    push = PushNotificationService()
    prefs = NotificationPreferencesDTO(
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
    alarm, _ = engine.trigger_or_update(
        rule_code="NODE_SILENT",
        severity="CRITICAL",
        entity_type="NODE",
        entity_id="A84041B8D2517C21",
        entity_name="Nodo Cámara 02",
        title="Nodo sin comunicación",
        condition_description="Sin actividad",
        observed_value="42 min",
        threshold_formatted="30 min",
        last_activity_at=datetime.now(timezone.utc),
    )

    # Primer envío se procesa
    first_send = await push.dispatch_alarm_push(alarm, "user-1", ["mock-fcm-token"], prefs)
    assert len(first_send) == 1

    # Segundo intento para la misma alarma y usuario se bloquea por deduplicación
    second_send = await push.dispatch_alarm_push(alarm, "user-1", ["mock-fcm-token"], prefs)
    assert len(second_send) == 0


def test_unavailable_metadata_formatting():
    assert format_battery(None, None) == "No disponible"
    has_coords, loc_text = format_location(None, None)
    assert has_coords is False
    assert loc_text == "No disponible"


def test_seconds_based_inactivity_evaluation():
    now = datetime.now(timezone.utc)
    threshold_seconds = 45

    # 1. Dispositivo activo (20 segundos transcurridos <= 45s)
    state_act, _, criteria_act, _ = evaluate_communication_state(
        now - timedelta(seconds=20), threshold_seconds, now, unit="seconds"
    )
    assert state_act == "ACTIVE"
    assert "20s" in criteria_act
    assert "≤ 45s" in criteria_act

    # 2. Dispositivo inactivo (60 segundos transcurridos > 45s)
    past_time = now - timedelta(seconds=60)
    state_sil, _, criteria_sil, _ = evaluate_communication_state(
        past_time, threshold_seconds, now, unit="seconds"
    )
    assert state_sil == "NO_COMMUNICATION"
    assert "60s" in criteria_sil
    assert "45s" in criteria_sil
    assert past_time.strftime("%Y-%m-%d %H:%M:%S UTC") in criteria_sil


def test_alarm_reports_last_activity_timestamp():
    engine = OperationalAlarmEngine()
    now = datetime.now(timezone.utc)
    last_seen = now - timedelta(seconds=90)
    ts_expected = last_seen.strftime("%Y-%m-%d %H:%M:%S UTC")

    alarm, is_new = engine.trigger_or_update(
        rule_code="NODE_SILENT",
        severity="CRITICAL",
        entity_type="NODE",
        entity_id="24e124746f420431",
        entity_name="CT-1000A",
        title="Dispositivo inactivo: CT-1000A",
        condition_description=f"Inactivo durante 90s. Última comunicación: {ts_expected}.",
        observed_value=f"Última comunicación: {ts_expected} (hace 90s)",
        threshold_formatted="60 segundos",
        last_activity_at=last_seen,
        now=now,
    )

    assert is_new is True
    assert alarm.last_activity_at == last_seen
    assert alarm.last_activity_timestamp == ts_expected
    assert ts_expected in alarm.condition_description
    assert ts_expected in alarm.observed_value
    assert alarm.age_seconds == 0
