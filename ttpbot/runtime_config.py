from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urlsplit

from .config import DEFAULT_SCHEDULE_URL
from .provider import ProviderConfigurationError, RacetimeProvider


class RuntimeConfigurationError(ValueError):
    """Runtime configuration is invalid without exposing secret values."""


WEBHOOK_PATH = re.compile(r"^/api/webhooks/[0-9]+/[^/?#]+$")
ENVIRONMENTS = {"development", "test", "production"}


@dataclass(frozen=True, repr=False)
class BotRuntimeConfig:
    provider: Optional[RacetimeProvider]
    client_id: Optional[str]
    client_secret: Optional[str]
    discord_webhook_url: Optional[str]
    race_seekers_role_id: Optional[str]
    data_dir: Optional[str]
    environment: str
    league_enabled: bool = False
    league_schedule_url: Optional[str] = None
    league_discord_webhook_url: Optional[str] = None
    _origin: Optional[str] = field(default=None, repr=False)
    _category: Optional[str] = field(default=None, repr=False)

    @property
    def category_slug(self):
        return self.provider.category if self.provider else self._category

    @property
    def announcements_enabled(self):
        return bool(self.discord_webhook_url and self.race_seekers_role_id)

    @property
    def league_announcements_enabled(self):
        return bool(self.league_enabled and self.league_discord_webhook_url)

    def __repr__(self):
        origin = self.provider.origin if self.provider else self._origin
        return (
            "BotRuntimeConfig(origin={!r}, category={!r}, environment={!r}, "
            "client_configured={}, announcements_enabled={}, data_dir={!r})"
        ).format(
            origin,
            self.category_slug,
            self.environment,
            bool(self.client_id and self.client_secret),
            self.announcements_enabled,
            self.data_dir,
        )


def _clean(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _arg_or_env(args, attr, env, env_name):
    return _clean(getattr(args, attr, None)) or _clean(env.get(env_name))


def _boolean(value, name):
    if isinstance(value, bool):
        return value
    cleaned = _clean(value)
    if cleaned is None:
        return False
    normalized = cleaned.lower()
    if normalized in ("1", "true", "yes"):
        return True
    if normalized in ("0", "false", "no"):
        return False
    raise RuntimeConfigurationError("{} must be true or false".format(name))


def _validate_webhook(value):
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuntimeConfigurationError("Discord announcement configuration is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "discord.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not WEBHOOK_PATH.fullmatch(parsed.path)
    ):
        raise RuntimeConfigurationError("Discord announcement configuration is invalid")
    return value


def resolve_bot_config(args, env=None):
    source = os.environ if env is None else env
    environment = _arg_or_env(args, "environment", source, "TTPBOT_ENVIRONMENT") or "development"
    if environment not in ENVIRONMENTS:
        raise RuntimeConfigurationError("TTPBOT_ENVIRONMENT is invalid")
    origin = _arg_or_env(args, "origin", source, "TTPBOT_RACETIME_ORIGIN")
    category = _arg_or_env(args, "category_slug", source, "TTPBOT_CATEGORY_SLUG")
    deprecated_host = _clean(getattr(args, "host", None))
    deprecated_insecure = bool(getattr(args, "insecure", False))
    if deprecated_host or deprecated_insecure:
        if environment == "production":
            raise RuntimeConfigurationError("deprecated host/insecure flags are forbidden in production")
        if origin:
            raise RuntimeConfigurationError("origin cannot be combined with deprecated host flags")
        if not deprecated_host:
            raise RuntimeConfigurationError("deprecated insecure flag requires a host")
        origin = "{}://{}".format("http" if deprecated_insecure else "https", deprecated_host)
    allow_value = getattr(args, "allow_insecure_loopback", None)
    allow_insecure = _boolean(
        allow_value if allow_value is not None else source.get("TTPBOT_ALLOW_INSECURE_LOOPBACK"),
        "TTPBOT_ALLOW_INSECURE_LOOPBACK",
    )
    if environment == "production" and allow_insecure:
        raise RuntimeConfigurationError("insecure loopback is forbidden in production")
    if not origin and environment != "production":
        origin = "https://racetime.gg"
    provider = None
    if origin and category:
        try:
            provider = RacetimeProvider(origin, category, allow_insecure)
        except ProviderConfigurationError as exc:
            raise RuntimeConfigurationError("Racetime provider configuration is invalid") from exc
    webhook = _validate_webhook(
        _arg_or_env(args, "discord_webhook_url", source, "TTPBOT_DISCORD_WEBHOOK_URL")
    )
    role_id = _arg_or_env(args, "race_seekers_role_id", source, "TTPBOT_RACE_SEEKERS_ROLE_ID")
    if bool(webhook) != bool(role_id):
        raise RuntimeConfigurationError("Discord webhook and role ID must be configured together")
    if role_id and (not role_id.isdigit() or len(role_id) > 30):
        raise RuntimeConfigurationError("Discord announcement configuration is invalid")
    configured_data_dir = _arg_or_env(args, "data_dir", source, "TTPBOT_DATA_DIR")
    if not configured_data_dir and environment != "production":
        configured_data_dir = str(Path(__file__).resolve().parent.parent)

    league_enabled = _boolean(
        _arg_or_env(args, "league_enabled", source, "TTPBOT_LEAGUE_ENABLED"),
        "TTPBOT_LEAGUE_ENABLED",
    )
    league_schedule_url = (
        _arg_or_env(args, "league_schedule_url", source, "TTPBOT_LEAGUE_SCHEDULE_URL")
        or DEFAULT_SCHEDULE_URL
    )
    league_webhook = _validate_webhook(
        _arg_or_env(args, "league_discord_webhook_url", source,
                    "TTPBOT_LEAGUE_DISCORD_WEBHOOK_URL")
    )
    return BotRuntimeConfig(
        provider=provider,
        client_id=_arg_or_env(args, "client_id", source, "TTPBOT_RACETIME_CLIENT_ID"),
        client_secret=_arg_or_env(args, "client_secret", source, "TTPBOT_RACETIME_CLIENT_SECRET"),
        discord_webhook_url=webhook,
        race_seekers_role_id=role_id,
        data_dir=configured_data_dir,
        environment=environment,
        league_enabled=league_enabled,
        league_schedule_url=league_schedule_url,
        league_discord_webhook_url=league_webhook,
        _origin=origin,
        _category=category,
    )


def missing_config_names(config):
    missing = []
    if not config._origin:
        missing.append("TTPBOT_RACETIME_ORIGIN")
    if not config._category:
        missing.append("TTPBOT_CATEGORY_SLUG")
    if not config.client_id:
        missing.append("TTPBOT_RACETIME_CLIENT_ID")
    if not config.client_secret:
        missing.append("TTPBOT_RACETIME_CLIENT_SECRET")
    if not config.data_dir:
        missing.append("TTPBOT_DATA_DIR")
    return missing
