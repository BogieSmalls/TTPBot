import argparse
import logging
import sys

from .bot import TTPBot
from .runtime_config import (
    RuntimeConfigurationError,
    missing_config_names,
    resolve_bot_config,
)


def main():
    parser = argparse.ArgumentParser(
        description='Triforce Triple Play scheduler for a configured Racetime provider',
    )
    parser.add_argument('legacy_category_slug', nargs='?',
                        help='deprecated positional category slug')
    parser.add_argument('client_id', nargs='?',
                        help='Racetime OAuth2 client ID (or TTPBOT_RACETIME_CLIENT_ID)')
    parser.add_argument('client_secret', nargs='?',
                        help='Racetime OAuth2 client secret (or TTPBOT_RACETIME_CLIENT_SECRET)')
    parser.add_argument('--origin', help='validated Racetime provider origin')
    parser.add_argument('--category', dest='category_slug', help='Racetime category slug')
    parser.add_argument('--environment', choices=('development', 'test', 'production'))
    parser.add_argument('--data-dir')
    parser.add_argument('--discord-webhook-url')
    parser.add_argument('--race-seekers-role-id')
    parser.add_argument('--allow-insecure-loopback', action='store_true', default=None,
                        help='allow HTTP only on localhost/127.0.0.1 outside production')
    parser.add_argument('--check-config', action='store_true',
                        help='validate configuration and exit without network access')
    parser.add_argument('--probe', action='store_true',
                        help='run the read-only provider preflight and exit')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--host', type=str, default=None,
                        help='deprecated development-only hostname override')
    parser.add_argument('--insecure', action='store_true',
                        help='deprecated development-only HTTP/WS override')

    args = parser.parse_args()
    if not args.category_slug:
        args.category_slug = args.legacy_category_slug
    try:
        config = resolve_bot_config(args)
    except RuntimeConfigurationError as exc:
        parser.error(str(exc))
    missing = missing_config_names(config)
    if missing:
        parser.error('missing required config: ' + ', '.join(missing))
    if args.check_config:
        print('TTPBOT_CONFIG=PASS destination={}'.format(config.provider.destination_key))
        return 0
    if args.probe:
        from .preflight import run_preflight
        return run_preflight(config)

    logger = logging.getLogger('ttpbot')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(name)s (%(levelname)s) :: %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    TTPBot.racetime_host = config.provider.host
    TTPBot.racetime_secure = config.provider.secure

    bot = TTPBot(
        category_slug=config.provider.category,
        client_id=config.client_id,
        client_secret=config.client_secret,
        logger=logger,
        provider=config.provider,
        discord_webhook_url=config.discord_webhook_url,
        race_seekers_role_id=config.race_seekers_role_id,
        data_dir=config.data_dir,
    )
    bot.run()
    return 0


if __name__ == '__main__':
    main()
