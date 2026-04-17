import argparse
import logging
import sys

from .bot import TTPBot


def main():
    parser = argparse.ArgumentParser(
        description='TTP Season 4 Bot for racetime.gg Z1R races',
    )
    parser.add_argument('category_slug', help='racetime.gg category slug (z1r)')
    parser.add_argument('client_id', help='racetime.gg OAuth2 client ID')
    parser.add_argument('client_secret', help='racetime.gg OAuth2 client secret')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--host', type=str, default=None,
                        help='Override racetime.gg hostname (debug only)')
    parser.add_argument('--insecure', action='store_true',
                        help='Use HTTP/WS instead of HTTPS/WSS (debug only)')

    args = parser.parse_args()

    logger = logging.getLogger('ttpbot')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(name)s (%(levelname)s) :: %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if args.host:
        TTPBot.racetime_host = args.host
    if args.insecure:
        TTPBot.racetime_secure = False

    bot = TTPBot(
        category_slug=args.category_slug,
        client_id=args.client_id,
        client_secret=args.client_secret,
        logger=logger,
    )
    bot.run()


if __name__ == '__main__':
    main()
