import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    decaid_host: str
    decaid_rest_port: int
    decaid_ws_port: int
    decaid_webui_port: int
    portal_host: str
    portal_port: int
    portal_session_idle_minutes: int
    admin_password: str

    @property
    def decaid_rest_base(self) -> str:
        return f"http://{self.decaid_host}:{self.decaid_rest_port}"

    @property
    def decaid_ws_url(self) -> str:
        return f"ws://{self.decaid_host}:{self.decaid_ws_port}/ws/v1/machine/shotState"

    @property
    def decaid_webui_base(self) -> str:
        return f"http://{self.decaid_host}:{self.decaid_webui_port}"


def get_settings() -> Settings:
    return Settings(
        database_url=_env("DATABASE_URL", "sqlite:///espressolab.db"),
        decaid_host=_env("DECAID_HOST", "localhost"),
        decaid_rest_port=int(_env("DECAID_REST_PORT", "8080")),
        decaid_ws_port=int(_env("DECAID_WS_PORT", "8080")),
        decaid_webui_port=int(_env("DECAID_WEBUI_PORT", "3000")),
        portal_host=_env("PORTAL_HOST", "0.0.0.0"),
        portal_port=int(_env("PORTAL_PORT", "5000")),
        portal_session_idle_minutes=int(_env("PORTAL_SESSION_IDLE_MINUTES", "15")),
        admin_password=_env("ADMIN_PASSWORD", "change-me"),
    )
