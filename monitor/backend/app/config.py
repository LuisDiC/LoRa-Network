from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "production"
    demo_mode: bool = False
    database_url: str = "postgresql+asyncpg://lorawan_admin:change_me@localhost:5432/lorawan_monitor"

    jwt_secret_key: str = "change_me_in_production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480

    # Credenciales exclusivas en el servidor (nunca enviadas al móvil)
    chirpstack_api_url: str = "http://localhost:8090"
    chirpstack_api_token: str = ""
    chirpstack_mqtt_host: str = "localhost"
    chirpstack_mqtt_port: int = 1883
    chirpstack_mqtt_user: str = ""
    chirpstack_mqtt_username: str = ""
    chirpstack_mqtt_password: str = ""
    chirpstack_verify_ssl: bool = True

    default_node_silent_minutes: int = 30
    default_gateway_silent_minutes: int = 15
    default_low_battery_percent: int = 20
    sync_interval_seconds: int = 60

    fcm_project_id: str = ""
    fcm_service_account_json_path: str = ""
    expo_access_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
