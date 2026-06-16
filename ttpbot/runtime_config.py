from dataclasses import dataclass
import os
from typing import Optional


@dataclass(frozen=True)
class BotRuntimeConfig:
    category_slug: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]


def _clean(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _arg_or_env(args, attr, env, env_name):
    return _clean(getattr(args, attr, None)) or _clean(env.get(env_name))


def resolve_bot_config(args, env=None):
    source = os.environ if env is None else env
    return BotRuntimeConfig(
        category_slug=_arg_or_env(args, 'category_slug', source, 'TTPBOT_CATEGORY_SLUG'),
        client_id=_arg_or_env(args, 'client_id', source, 'TTPBOT_RACETIME_CLIENT_ID'),
        client_secret=_arg_or_env(args, 'client_secret', source, 'TTPBOT_RACETIME_CLIENT_SECRET'),
    )


def missing_config_names(config):
    missing = []
    if not config.category_slug:
        missing.append('TTPBOT_CATEGORY_SLUG')
    if not config.client_id:
        missing.append('TTPBOT_RACETIME_CLIENT_ID')
    if not config.client_secret:
        missing.append('TTPBOT_RACETIME_CLIENT_SECRET')
    return missing
