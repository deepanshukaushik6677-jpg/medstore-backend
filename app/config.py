from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://neondb_owner:npg_k4C3hRdUAYzD@ep-silent-field-aw11fy8n.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    environment: str = "development"

    # Comma-separated list, e.g. "http://localhost:5173,https://your-project.pages.dev"
    cors_origins: str = "http://localhost:5173,https://medstore-frontend.medstore.workers.dev/"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
