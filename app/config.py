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
settings = Settings()
