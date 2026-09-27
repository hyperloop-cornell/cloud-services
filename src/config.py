"""Configuration management."""
import os
import logging
from pathlib import Path
from typing import Dict, List, Set, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

logger = logging.getLogger(__name__)

DEFAULT_JWT_SECRET = "your-secret-key-change-this-in-production"

# Development-only fallbacks. validate_for_environment() refuses all of these in production.
DEV_NETIDS = {"dev"}
DEV_TEAM_PASSWORD = "hyperloop-dev"
DEV_DEVICE_TOKENS = {
    "dev-token-rpi-bridge-01": "rpi-bridge-01",
    "dev-token-rpi-bridge-02": "rpi-bridge-02",
}

# NetIDs that would be confusing as identities (the viewer login uses "viewer" as its subject)
RESERVED_NETIDS = {"viewer", "admin", "root"}


def normalize_netid(raw: str) -> str:
    """Lowercase a NetID and strip an optional @cornell.edu suffix."""
    netid = (raw or "").strip().lower()
    if netid.endswith("@cornell.edu"):
        netid = netid[: -len("@cornell.edu")]
    return netid


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    environment: str = "development"

    # JWT
    jwt_secret_key: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480

    # Login: NetID allowlist + one shared team password (bcrypt hash)
    allowed_netids: str = ""
    allowed_netids_file: str = ""
    team_password_hash: str = ""

    # Login rate limiting (failed attempts per window)
    login_max_failures_per_netid: int = 5
    login_max_failures_per_ip: int = 20
    login_failure_window_seconds: int = 300
    # Only enable behind a reverse proxy that sets these headers and when the API
    # port is not reachable directly; otherwise clients could spoof their IP.
    trust_proxy_headers: bool = False

    # Device tokens: "hub-id:token,hub-id:token"
    device_tokens: str = ""
    # Legacy single-hub token variables, still honored so existing .env files keep working
    device_token_rpi_bridge_01: str = ""
    device_token_rpi_bridge_02: str = ""

    # CORS - can be string or list
    cors_origins: Union[str, List[str]] = "http://localhost:3000,http://localhost:4173,http://localhost:5173,http://localhost:8080,https://gui.cornellhyperloop.com"

    @field_validator('cors_origins', mode='after')
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from comma-separated string to list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(',') if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def get_allowed_netids(self) -> Set[str]:
        """NetIDs allowed to log in, from ALLOWED_NETIDS and/or ALLOWED_NETIDS_FILE."""
        netids: Set[str] = set()

        for part in self.allowed_netids.split(","):
            if part.strip():
                netids.add(normalize_netid(part))

        if self.allowed_netids_file:
            path = Path(self.allowed_netids_file)
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    entry = line.split("#", 1)[0].strip()
                    if entry:
                        netids.add(normalize_netid(entry))
            except OSError as e:
                logger.error(f"Could not read ALLOWED_NETIDS_FILE {path}: {e}")

        netids -= RESERVED_NETIDS
        if not netids and not self.is_production:
            return set(DEV_NETIDS)
        return netids

    def get_valid_device_tokens(self) -> Dict[str, str]:
        """Get mapping of device tokens to hub IDs."""
        tokens: Dict[str, str] = {}

        for pair in self.device_tokens.split(","):
            pair = pair.strip()
            if not pair:
                continue
            hub_id, sep, token = pair.partition(":")
            if not sep or not hub_id.strip() or not token.strip():
                logger.error("Ignoring malformed DEVICE_TOKENS entry (expected hub-id:token)")
                continue
            tokens[token.strip()] = hub_id.strip()

        if self.device_token_rpi_bridge_01:
            tokens[self.device_token_rpi_bridge_01] = "rpi-bridge-01"
        if self.device_token_rpi_bridge_02:
            tokens[self.device_token_rpi_bridge_02] = "rpi-bridge-02"

        if not tokens and not self.is_production:
            return dict(DEV_DEVICE_TOKENS)
        return tokens

    def validate_for_environment(self) -> None:
        """Refuse to run production with missing or development credentials."""
        if not self.is_production:
            return

        problems = []
        if not self.jwt_secret_key or self.jwt_secret_key == DEFAULT_JWT_SECRET or len(self.jwt_secret_key) < 32:
            problems.append("JWT_SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.team_password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            problems.append("TEAM_PASSWORD_HASH must be a bcrypt hash")
        if not self.get_allowed_netids():
            problems.append("ALLOWED_NETIDS or ALLOWED_NETIDS_FILE must list at least one NetID")
        tokens = self.get_valid_device_tokens()
        if not tokens:
            problems.append("DEVICE_TOKENS must define at least one hub")
        if any(token in DEV_DEVICE_TOKENS or token.startswith("dev-token") for token in tokens):
            problems.append("DEVICE_TOKENS must not use development tokens")

        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))

        for token, hub_id in tokens.items():
            if len(token) < 24:
                logger.warning(f"Device token for {hub_id} is short; regenerate it (see .claude/auth-setup.md)")


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get application settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Drop the cached settings (used by tests)."""
    global _settings
    _settings = None
