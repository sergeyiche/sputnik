from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    llm_provider: str = "gigachat"
    vector_store: str = "chroma"
    rag_top_k: int = 5
    session_cookie_name: str = "parkinson_session"
    session_max_messages: int = 20

    admin_username: str = ""
    admin_password: str = ""
    admin_session_ttl_minutes: int = 480
    admin_cookie_name: str = "oracool_admin"
    admin_cookie_secure: bool = False
    admin_max_upload_mb: int = 50
    platform_base_domain: str = "spootnik.ru"
    platform_db_path: str = ""
    data_root: str = ""

    mail_transport: str = "smtp"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    mailtrap_api_key: str = ""
    mailtrap_use_sandbox: bool = True
    mailtrap_inbox_id: str = ""
    mailtrap_sender_email: str = "noreply@spootnik.ru"
    mailtrap_sender_name: str = "Спутник"
    password_reset_ttl_minutes: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
