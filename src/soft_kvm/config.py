"""Runtime configuration.

Phase 0 reads a throwaway SmartThings Personal Access Token (PAT) from the
environment or a local, gitignored ``.env`` file. PATs created after
30 Dec 2024 expire in 24h, so this is strictly for discovery — the durable
Path B solution (Phase 2) replaces it with the OAuth2 refresh-token flow.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError


class Settings(BaseSettings):
    """Process configuration, loaded from environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Throwaway Phase 0/1 credential. Optional so the package imports without it;
    # commands that need it call ``require_pat()`` and fail loudly if absent.
    smartthings_pat: SecretStr | None = None
    smartthings_api_base: str = "https://api.smartthings.com/v1"
    soft_kvm_log_level: str = "info"

    # Phase 2 — durable OAuth2 auth. Static client credentials live in 1Password and
    # are injected at runtime (e.g. `op run`); the rotating refresh token lives in the
    # macOS Keychain (see auth.py). Endpoints verified against the live SmartThings docs.
    smartthings_client_id: str | None = None
    smartthings_client_secret: SecretStr | None = None
    # SmartThings' gateway REJECTS localhost redirect URIs (403 at /oauth/authorize),
    # so this must be a public HTTPS URL registered on the app. The flow still ends
    # with manually copying the `code` from the redirect URL's query string.
    smartthings_redirect_uri: str = "https://mikenoe.com"
    smartthings_scopes: str = "r:devices:* x:devices:*"
    smartthings_oauth_authorize_url: str = "https://api.smartthings.com/oauth/authorize"
    smartthings_oauth_token_url: str = "https://api.smartthings.com/oauth/token"

    def require_pat(self) -> SecretStr:
        """Return the PAT or raise a clear, actionable error."""
        if self.smartthings_pat is None or not self.smartthings_pat.get_secret_value():
            raise ConfigError(
                "SMARTTHINGS_PAT is not set. For Phase 0 discovery, generate a "
                "throwaway Personal Access Token at "
                "https://account.smartthings.com/tokens and put it in a local "
                "`.env` file (copy from `.env.example`) or export it in the shell."
            )
        return self.smartthings_pat

    def has_oauth_credentials(self) -> bool:
        """True if OAuth client_id + client_secret are both configured."""
        return bool(
            self.smartthings_client_id
            and self.smartthings_client_secret
            and self.smartthings_client_secret.get_secret_value()
        )

    def require_oauth(self) -> tuple[str, SecretStr]:
        """Return (client_id, client_secret) or raise a clear, actionable error."""
        if not self.has_oauth_credentials():
            raise ConfigError(
                "SMARTTHINGS_CLIENT_ID / SMARTTHINGS_CLIENT_SECRET are not set. Register an "
                "OAuth app in the SmartThings Developer Workspace, then provide the credentials "
                "via 1Password (`op run`) or a local `.env`. See docs/PHASE2_AUTH.md."
            )
        # mypy: has_oauth_credentials() guarantees these are non-None.
        assert self.smartthings_client_id is not None
        assert self.smartthings_client_secret is not None
        if self.smartthings_client_id.startswith(
            "op://"
        ) or self.smartthings_client_secret.get_secret_value().startswith("op://"):
            raise ConfigError(
                "OAuth client credentials look like unresolved 1Password references (op://…). "
                "Run the command through 1Password so they resolve, e.g.:\n"
                "  op run --env-file .env -- uv run soft-kvm <command>"
            )
        return self.smartthings_client_id, self.smartthings_client_secret


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()


def project_root() -> Path:
    """Locate the repo root by walking up from this file to the dir holding pyproject.toml.

    Used to resolve the fixtures/docs directories without hardcoding absolute paths,
    so the tool keeps working when the repo is moved or deployed elsewhere.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    # Fallback: current working directory.
    return Path.cwd()


def fixtures_dir() -> Path:
    """Directory where raw API responses are saved as Phase 4 test fixtures."""
    return project_root() / "tests" / "fixtures"


def docs_dir() -> Path:
    """Directory for generated documentation (e.g. FINDINGS.md)."""
    return project_root() / "docs"
