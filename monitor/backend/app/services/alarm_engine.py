from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import uuid
from app.schemas import AlarmDTO, AlarmSeverity, EntityType, OperationalEventDTO

SEVERITY_RANK: Dict[AlarmSeverity, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}

NODE_SILENT_SNOOZE_MINUTES = 20


class OperationalAlarmEngine:
    """
    Motor de reglas operativas con deduplicación estricta, control de estados
    (ACTIVE -> ACKNOWLEDGED -> RESOLVED), silenciamiento temporal de 20 minutos
    para alarmas de dispositivo inactivo (NODE_SILENT) y registro de auditoría.
    Jamás evalúa variables medidas por sensores.
    """

    def __init__(self) -> None:
        # Clave de deduplicación: (rule_code, entity_type, entity_id) -> AlarmDTO
        self.active_alarms: Dict[Tuple[str, EntityType, str], AlarmDTO] = {}
        # Silenciamiento temporal tras reconocer alarma: (rule_code, entity_type, entity_id) -> datetime (UTC)
        self.silenced_until: Dict[Tuple[str, EntityType, str], datetime] = {}
        self.silenced_alarms: Dict[Tuple[str, EntityType, str], AlarmDTO] = {}
        self.resolved_alarms: List[AlarmDTO] = []
        self.events: List[OperationalEventDTO] = []

    def is_silenced(
        self,
        rule_code: str,
        entity_type: EntityType,
        entity_id: str,
        now: Optional[datetime] = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        key = (rule_code, entity_type, entity_id)
        until = self.silenced_until.get(key)
        if not until:
            return False
        if current_time < until:
            return True
        # Expiró la ventana de 20 minutos: limpiar para permitir nueva evaluación
        self.silenced_until.pop(key, None)
        self.silenced_alarms.pop(key, None)
        return False

    def trigger_or_update(
        self,
        rule_code: str,
        severity: AlarmSeverity,
        entity_type: EntityType,
        entity_id: str,
        entity_name: str,
        title: str,
        condition_description: str,
        observed_value: str,
        threshold_formatted: str,
        last_activity_at: Optional[datetime],
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[AlarmDTO], bool]:
        """
        Retorna (AlarmDTO | None, is_new).
        Si la alarma fue reconocida y se encuentra dentro de la ventana de silencio
        de 20 minutos (para NODE_SILENT), no se evalúa ni se muestra hasta que expire.
        """
        current_time = now or datetime.now(timezone.utc)
        key = (rule_code, entity_type, entity_id)

        # Si está silenciada temporalmente (20 min), omitir evaluación y ocultarla
        if self.is_silenced(rule_code, entity_type, entity_id, current_time):
            if key in self.active_alarms:
                self.active_alarms.pop(key, None)
            return None, False

        ts_str = (
            last_activity_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            if last_activity_at
            else "Sin comunicación registrada"
        )

        if key in self.active_alarms:
            existing = self.active_alarms[key]
            existing.observed_value = observed_value
            existing.condition_description = condition_description
            existing.last_activity_at = last_activity_at
            existing.last_activity_timestamp = ts_str
            existing.threshold_formatted = threshold_formatted
            diff_secs = max(0, int((current_time - existing.detected_at).total_seconds()))
            existing.age_seconds = diff_secs
            existing.age_minutes = diff_secs // 60
            return existing, False

        new_alarm = AlarmDTO(
            id=str(uuid.uuid4()),
            rule_code=rule_code,
            severity=severity,
            status="ACTIVE",
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            title=title,
            condition_description=condition_description,
            observed_value=observed_value,
            threshold_formatted=threshold_formatted,
            last_activity_at=last_activity_at,
            last_activity_timestamp=ts_str,
            detected_at=current_time,
            age_minutes=0,
            age_seconds=0,
        )
        self.active_alarms[key] = new_alarm
        self._record_event(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            event_type="ALARM_TRIGGERED",
            previous_state="NORMAL",
            new_state=f"ALARM_{severity}",
            detail=f"{title}: {observed_value} (umbral {threshold_formatted})",
            occurred_at=current_time,
        )
        return new_alarm, True

    def resolve_if_recovered(
        self,
        rule_code: str,
        entity_type: EntityType,
        entity_id: str,
        recovery_detail: str,
        now: Optional[datetime] = None,
    ) -> Optional[AlarmDTO]:
        current_time = now or datetime.now(timezone.utc)
        key = (rule_code, entity_type, entity_id)
        # Si el nodo se recupera, limpiar cualquier silenciamiento activo
        self.silenced_until.pop(key, None)
        alarm = self.active_alarms.pop(key, None) or self.silenced_alarms.pop(key, None)
        if not alarm:
            return None

        alarm.status = "RESOLVED"
        alarm.resolved_at = current_time
        self.resolved_alarms.insert(0, alarm)
        self._record_event(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=alarm.entity_name,
            event_type="ALARM_RESOLVED",
            previous_state=alarm.severity,
            new_state="RESOLVED",
            detail=recovery_detail,
            occurred_at=current_time,
        )
        return alarm

    def acknowledge(self, alarm_id: str, operator_name: str, now: Optional[datetime] = None) -> Optional[AlarmDTO]:
        current_time = now or datetime.now(timezone.utc)
        for key, alarm in list(self.active_alarms.items()):
            if alarm.id == alarm_id:
                if alarm.status == "ACTIVE":
                    alarm.status = "ACKNOWLEDGED"
                    alarm.acknowledged_at = current_time
                    alarm.acknowledged_by = operator_name

                    if alarm.rule_code == "NODE_SILENT":
                        # Silenciar y ocultar esta alarma específica de dispositivo inactivo durante 20 minutos
                        self.silenced_until[key] = current_time + timedelta(minutes=NODE_SILENT_SNOOZE_MINUTES)
                        self.silenced_alarms[key] = alarm
                        self.active_alarms.pop(key, None)
                        self._record_event(
                            entity_type=alarm.entity_type,
                            entity_id=alarm.entity_id,
                            entity_name=alarm.entity_name,
                            event_type="ALARM_ACKNOWLEDGED",
                            previous_state="ACTIVE",
                            new_state="SILENCED_20M",
                            detail=f"Reconocida por {operator_name} (silenciada por {NODE_SILENT_SNOOZE_MINUTES} min)",
                            occurred_at=current_time,
                        )
                    else:
                        self._record_event(
                            entity_type=alarm.entity_type,
                            entity_id=alarm.entity_id,
                            entity_name=alarm.entity_name,
                            event_type="ALARM_ACKNOWLEDGED",
                            previous_state="ACTIVE",
                            new_state="ACKNOWLEDGED",
                            detail=f"Reconocida por {operator_name}",
                            occurred_at=current_time,
                        )
                return alarm
        return None

    def manual_resolve(self, alarm_id: str, operator_name: str, now: Optional[datetime] = None) -> Optional[AlarmDTO]:
        current_time = now or datetime.now(timezone.utc)
        for key, alarm in list(self.active_alarms.items()):
            if alarm.id == alarm_id:
                return self.resolve_if_recovered(
                    alarm.rule_code,
                    alarm.entity_type,
                    alarm.entity_id,
                    f"Cerrada manualmente por {operator_name}",
                    now=current_time,
                )
        return None

    def get_highest_priority_alarm(self) -> Optional[AlarmDTO]:
        active_list = list(self.active_alarms.values())
        if not active_list:
            return None
        # Prioriza: 1) No reconocidas primero, 2) Mayor gravedad, 3) Más reciente
        active_list.sort(
            key=lambda a: (
                1 if a.status == "ACTIVE" else 0,
                SEVERITY_RANK[a.severity],
                a.detected_at.timestamp(),
            ),
            reverse=True,
        )
        return active_list[0]

    def _record_event(
        self,
        entity_type: EntityType,
        entity_id: str,
        entity_name: str,
        event_type: str,
        previous_state: Optional[str],
        new_state: str,
        detail: str,
        occurred_at: datetime,
    ) -> None:
        self.events.insert(
            0,
            OperationalEventDTO(
                id=str(uuid.uuid4()),
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                event_type=event_type,
                previous_state=previous_state,
                new_state=new_state,
                detail=detail,
                occurred_at=occurred_at,
            ),
        )
        self.events = self.events[:250]
