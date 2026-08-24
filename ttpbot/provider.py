"""Validated, immutable Racetime provider and URL resolution contract."""

from dataclasses import dataclass, field
import ipaddress
import re
from urllib.parse import unquote, urlsplit, urlunsplit


class ProviderConfigurationError(ValueError):
    """A provider origin, category, or returned URL is unsafe."""


CATEGORY = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)


def _safe_path(path, label):
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or ENCODED_SEPARATOR.search(path)
    ):
        raise ProviderConfigurationError("{} path is invalid".format(label))
    decoded = unquote(path)
    if any(segment in (".", "..") for segment in decoded.split("/")):
        raise ProviderConfigurationError("{} path contains traversal".format(label))
    return path


def _split_url(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProviderConfigurationError("{} is invalid".format(label))
    try:
        parsed = urlsplit(value)
        # Accessing port forces validation of malformed port syntax.
        parsed.port
    except ValueError as exc:
        raise ProviderConfigurationError("{} is malformed".format(label)) from exc
    return parsed


@dataclass(frozen=True)
class RacetimeProvider:
    origin: str
    category: str
    allow_insecure_loopback: bool = field(default=False, repr=False)
    host: str = field(init=False)
    secure: bool = field(init=False)
    destination_key: str = field(init=False)
    _netloc: str = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.category, str) or not CATEGORY.fullmatch(self.category):
            raise ProviderConfigurationError("category is invalid")
        parsed = _split_url(self.origin, "provider origin")
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ProviderConfigurationError("provider origin is not an origin")
        hostname = parsed.hostname.lower()
        if hostname.endswith("."):
            raise ProviderConfigurationError("provider hostname is invalid")
        try:
            ipaddress.ip_address(hostname)
            is_ip_literal = True
        except ValueError:
            is_ip_literal = False
        secure = parsed.scheme == "https"
        if not secure:
            if not self.allow_insecure_loopback or hostname not in ("localhost", "127.0.0.1"):
                raise ProviderConfigurationError("HTTP is allowed only for explicit loopback tests")
        elif is_ip_literal:
            raise ProviderConfigurationError("production provider origins require a DNS hostname")

        default_port = 443 if secure else 80
        port = parsed.port
        if ":" in hostname:
            canonical_host = "[{}]".format(hostname)
        else:
            canonical_host = hostname
        netloc = canonical_host if port in (None, default_port) else "{}:{}".format(canonical_host, port)
        canonical_origin = urlunsplit((parsed.scheme, netloc, "", "", ""))
        object.__setattr__(self, "origin", canonical_origin)
        object.__setattr__(self, "host", netloc)
        object.__setattr__(self, "secure", secure)
        object.__setattr__(self, "destination_key", "{}|{}".format(canonical_origin, self.category))
        object.__setattr__(self, "_netloc", netloc)

    def _http_target(self, value, label):
        parsed = _split_url(value, label)
        if parsed.scheme or parsed.netloc:
            if (
                parsed.scheme != ("https" if self.secure else "http")
                or parsed.netloc.lower() != self._netloc.lower()
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ProviderConfigurationError("{} uses the wrong provider origin".format(label))
            path = parsed.path
        else:
            path = value
        if parsed.query or parsed.fragment:
            raise ProviderConfigurationError("{} query and fragment are not allowed".format(label))
        path = _safe_path(path, label)
        return urlunsplit(("https" if self.secure else "http", self._netloc, path, "", ""))

    def http_url(self, path):
        """Resolve an HTTP API path without permitting an origin change."""
        return self._http_target(path, "HTTP URL")

    def resolve_location(self, location):
        """Resolve a room Location and require this provider/category."""
        result = self._http_target(location, "room Location")
        path = urlsplit(result).path
        prefix = "/{}/".format(self.category)
        if not path.startswith(prefix) or len(path) <= len(prefix):
            raise ProviderConfigurationError("room Location uses the wrong category")
        return result

    def websocket_url(self, value):
        """Resolve a provider WebSocket URL with the matching transport."""
        parsed = _split_url(value, "WebSocket URL")
        expected_scheme = "wss" if self.secure else "ws"
        http_scheme = "https" if self.secure else "http"
        if parsed.scheme or parsed.netloc:
            if (
                parsed.scheme not in (expected_scheme, http_scheme)
                or parsed.netloc.lower() != self._netloc.lower()
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ProviderConfigurationError("WebSocket URL uses the wrong provider origin")
            path = parsed.path
        else:
            path = value
        if parsed.query or parsed.fragment:
            raise ProviderConfigurationError("WebSocket query and fragment are not allowed")
        path = _safe_path(path, "WebSocket URL")
        prefix = "/ws/{}/".format(self.category)
        if not path.startswith(prefix) or len(path) <= len(prefix):
            raise ProviderConfigurationError("WebSocket URL uses the wrong category")
        return urlunsplit((expected_scheme, self._netloc, path, "", ""))
