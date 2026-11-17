from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    llm_base_url: str = ''
    llm_api_key: str = ''
    llm_model: str = ''
    llm_timeout_seconds: float = 12.0
    embedding_base_url: str = ''
    embedding_api_key: str = ''
    embedding_model: str = ''
    embedding_timeout_seconds: float = 10.0
    database_url: str = 'postgresql+psycopg://dateideas:dateideas@localhost:5432/dateideas'
    telegram_bot_token: str = ''
    telegram_webhook_secret: str = ''
    admin_api_key: str = ''
    admin_username: str = 'admin'
    admin_password: str = ''
    admin_password_hash: str = ''
    admin_session_secret: str = ''
    public_base_url: str = ''
    minio_endpoint: str = 'minio:9000'
    minio_root_user: str = ''
    minio_root_password: str = ''
    minio_bucket: str = 'dateideas-media'
    minio_secure: bool = False
    telegram_edit_scan_limit: int = 100
    telegram_media_max_bytes: int = 8_000_000
    search_default_limit: int = 3
    moscow_timezone: str = 'Europe/Moscow'
    webhook_rate_limit_per_minute: int = 20
    # Раздел 9 спеки: базовые поля события. Соль обязательна для псевдонимизации;
    # без неё user_hash не записывается вовсе.
    analytics_salt: str = ''
    environment: str = 'production'
    is_test: bool = False
    # NFR-08: сроки хранения, дни.
    retention_dialogue_days: int = 30
    retention_analytics_days: int = 90
    # NFR-09: дневной бюджет вызовов LLM; 0 — без лимита.
    llm_max_calls_per_day: int = 0
    llm_max_output_tokens: int = 512
settings = Settings()
