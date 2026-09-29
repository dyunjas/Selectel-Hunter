from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids: list[int] = Field(default_factory=list, alias="ADMIN_IDS")
    encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")
    database_url: str = Field("sqlite+aiosqlite:///./selectel_hunter.db", alias="DATABASE_URL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    default_region: str = "ru-3"
    default_network_api: str = "https://ru-3.cloud.api.selcloud.ru/network/v2.0"
    floating_network_id: str = "966826e6-d301-4bb5-aa13-77a324d15f0d"
    default_min_interval: int = 30
    default_max_interval: int = 60
    min_allowed_interval: int = 30
    notification_chat_id: int = Field(0, alias="NOTIFICATION_CHAT_ID")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(x.strip()) for x in value.split(",") if x.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
