from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple
import logging
import httpx
from app.config import settings
from app.schemas import AlarmDTO, NotificationPreferencesDTO

logger = logging.getLogger("lorawan_monitor.push")


class PushNotificationService:
    """
    Servicio de notificaciones Push para Android (FCM) e iOS (APNs vía Expo Push Service).
    Incluye filtrado por preferencias de usuario, deduplicación por (alarm_id, user_id)
    y registro de errores de envío.
    """

    def __init__(self) -> None:
        # (alarm_id, user_id) ya notificados para evitar envíos repetidos
        self.sent_dedup: Set[Tuple[str, str]] = set()
        self.delivery_logs: List[Dict[str, str]] = []

    def should_notify_user(
        self, alarm: AlarmDTO, prefs: NotificationPreferencesDTO, user_id: str
    ) -> bool:
        if not prefs.push_enabled:
            return False
        if (alarm.id, user_id) in self.sent_dedup:
            return False

        severity_allowed = {
            "CRITICAL": prefs.notify_critical,
            "HIGH": prefs.notify_high,
            "MEDIUM": prefs.notify_medium,
            "LOW": prefs.notify_low,
        }.get(alarm.severity, False)

        if not severity_allowed:
            return False

        rule_allowed = {
            "NODE_SILENT": prefs.notify_node_silent,
            "GATEWAY_OFFLINE": prefs.notify_gateway_offline,
            "LOW_BATTERY": prefs.notify_low_battery,
            "CHIRPSTACK_DISCONNECTED": prefs.notify_chirpstack_loss,
            "INTEGRATION_ERROR": prefs.notify_chirpstack_loss,
        }.get(alarm.rule_code, True)

        return rule_allowed

    async def dispatch_alarm_push(
        self,
        alarm: AlarmDTO,
        user_id: str,
        tokens: List[str],
        prefs: NotificationPreferencesDTO,
    ) -> List[Dict[str, str]]:
        if not self.should_notify_user(alarm, prefs, user_id):
            return []

        results: List[Dict[str, str]] = []
        payload_data = {
            "alarmId": alarm.id,
            "entityType": alarm.entity_type,
            "entityId": alarm.entity_id,
            "severity": alarm.severity,
            "deepLink": f"lorawanmonitor://alarms/{alarm.id}",
        }

        for token in tokens:
            token_clean = token.strip()
            if not token_clean:
                continue
            status = "SENT"
            error_msg = ""
            try:
                # Expo Push Service soporta tokens ExponentPushToken[...] y ExpoPushToken[...]
                if "ExpoPushToken" in token_clean or "ExponentPushToken" in token_clean or token_clean.startswith("ExponentPushToken["):
                    headers: Dict[str, str] = {
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip, deflate",
                        "Content-Type": "application/json",
                    }
                    if settings.expo_access_token:
                        headers["Authorization"] = f"Bearer {settings.expo_access_token}"

                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.post(
                            "https://exp.host/--/api/v2/push/send",
                            headers=headers,
                            json={
                                "to": token_clean,
                                "sound": "default",
                                "channelId": "default",
                                "priority": "high",
                                "title": f"🚨 [{alarm.severity}] {alarm.title}",
                                "body": f"{alarm.entity_name} · {alarm.observed_value}",
                                "data": payload_data,
                            },
                        )
                        resp.raise_for_status()
                        logger.info("Push enviado vía Expo a %s para alarma %s", token_clean, alarm.id)
                else:
                    logger.warning("Token push omitido por formato no reconocido: %s", token_clean)
                    status = "SKIPPED"
                    error_msg = "Formato de token no compatible con Expo Push"
            except Exception as exc:
                status = "FAILED"
                error_msg = str(exc)
                logger.error("Error enviando push a %s: %s", token_clean, error_msg)

            record = {
                "alarm_id": alarm.id,
                "user_id": user_id,
                "token": token_clean,
                "status": status,
                "error": error_msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.delivery_logs.append(record)
            results.append(record)

        self.sent_dedup.add((alarm.id, user_id))
        return results
