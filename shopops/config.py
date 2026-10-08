from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres. The admin account is only used by setup/seed; the server itself uses the three roles below.
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "shopops"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    ro_password: str = "shopops_ro_dev"    # role shopops_ro  -> read tools (SELECT only)
    rw_password: str = "shopops_rw_dev"    # role shopops_rw  -> write tools (INSERT tickets, UPDATE order status)
    app_password: str = "shopops_app_dev"  # role shopops_app -> API keys, confirmations, audit log

    # MCP
    max_page_size: int = 100
    default_page_size: int = 20
    confirm_ttl_seconds: int = 300
    stdio_principal: str = "stdio:local"
    stdio_scope: str = "read_write"  # scope for the local stdio transport
    bootstrap_api_key: str = ""      # optional static key (read_write) for CI / MCP Inspector

    # Console auth (Google SSO)
    google_client_id: str = ""
    app_jwt_secret: str = "dev-only-change-me"
    app_jwt_ttl_hours: int = 24 * 7
    allowed_emails: str = ""
    allowed_domains: str = ""

    def url(self, role: str) -> str:
        users = {
            "admin": (self.postgres_user, self.postgres_password),
            "ro": ("shopops_ro", self.ro_password),
            "rw": ("shopops_rw", self.rw_password),
            "app": ("shopops_app", self.app_password),
        }
        user, pw = users[role]
        return f"postgresql+asyncpg://{user}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
