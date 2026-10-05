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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
