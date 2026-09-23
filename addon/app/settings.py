from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str

    ha_base_url: str
    ha_long_lived_token: str

    whatsapp_phone_number_id: str
    whatsapp_access_token: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str

    allowed_sender_numbers: str  # comma-separated E.164 numbers without leading +

    entities_config_path: str = "config/entities.yaml"
    confirmation_ttl_seconds: int = 120
    reminders_path: str = "/data/reminders.json"
    lists_path: str = "/data/lists.json"
    memory_path: str = "/data/memory.json"
    monitors_path: str = "/data/monitors.json"
    scheduled_actions_path: str = "/data/scheduled_actions.json"
    agenda_path: str = "/data/agenda.json"
    briefing_config_path: str = "/data/briefing_config.json"
    anchors_path: str = "/data/anchors.json"
    holidays_cache_path: str = "/data/holidays_cache.json"
    conversation_path: str = "/data/conversation.json"
    conversation_log_path: str = "/data/conversation_log.json"
    expenses_path: str = "/data/expenses.json"
    recurring_expenses_path: str = "/data/recurring_expenses.json"
    whisper_host: str = "192.168.10.150"
    whisper_port: int = 10300


settings = Settings()
