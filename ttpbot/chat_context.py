"""Publish exact League room format to the always-on chat owner. No chat sends."""
import ipaddress
import re
from urllib.parse import quote, urlsplit

import aiohttp


class ChatContextPublisher:
    def __init__(self, url, token, environment, logger):
        parsed = urlsplit(url)
        private_http = False
        try:
            private_http = ipaddress.ip_address(parsed.hostname or '').is_private
        except ValueError:
            pass
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in ('http', 'https') or (parsed.scheme == 'http' and not private_http)
                or len(token) < 32 or environment not in ('staging', 'production')):
            raise ValueError('Invalid chat context configuration')
        self.url = url.rstrip('/')
        self.token = token
        self.environment = environment
        self.logger = logger
        self._published = {}

    @classmethod
    def from_env(cls, env, logger):
        names = ('Z1RR_CHAT_CONTEXT_URL', 'Z1RR_CHAT_CONTEXT_TOKEN', 'Z1RR_CHAT_CONTEXT_ENVIRONMENT')
        values = [env.get(name, '').strip() for name in names]
        if not any(values):
            return None
        try:
            return cls(*values, logger)
        except (TypeError, ValueError):
            logger.warning('Chat room-format publishing is unconfigured; League scheduling continues')
            return None

    async def publish(self, room_url, source_id, coop):
        try:
            parsed = urlsplit(room_url)
            slug = parsed.path.strip('/')
            if (parsed.scheme != 'https' or parsed.netloc != 'racetime.gg' or parsed.query or parsed.fragment
                    or not re.fullmatch(r'z1r/[a-z0-9-]{1,160}', slug)
                    or not isinstance(source_id, str) or not re.fullmatch(r'[^\r\n]{1,250}', source_id)):
                return False
            payload = {'format': 'coop' if coop else 'single', 'sourceId': 'league:' + source_id}
            if self._published.get(slug) == payload:
                return True
            async with aiohttp.request(method='PUT',
                    url=self.url + '/v1/races/racetime-gg/' + quote(slug, safe='') + '/format',
                    headers={'Authorization': 'Bearer ' + self.token, 'X-Z1RR-Environment': self.environment},
                    json=payload, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False) as response:
                if response.status != 200:
                    self.logger.warning('Chat room-format publishing failed (HTTP %d); retrying next tick', response.status)
                    return False
            self._published[slug] = payload
            while len(self._published) > 2000:
                self._published.pop(next(iter(self._published)))
            return True
        except Exception as exc:
            self.logger.warning('Chat room-format publishing failed (%s); retrying next tick', type(exc).__name__)
            return False
