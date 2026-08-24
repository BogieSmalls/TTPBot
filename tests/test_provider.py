import unittest

from ttpbot.provider import ProviderConfigurationError, RacetimeProvider


class RacetimeProviderTests(unittest.TestCase):
    def test_canonical_https_provider_and_destination(self):
        provider = RacetimeProvider(
            origin="https://racetime.z1rracing.com/",
            category="z1rr",
        )
        self.assertEqual(provider.origin, "https://racetime.z1rracing.com")
        self.assertEqual(provider.host, "racetime.z1rracing.com")
        self.assertTrue(provider.secure)
        self.assertEqual(
            provider.destination_key,
            "https://racetime.z1rracing.com|z1rr",
        )
        with self.assertRaises((AttributeError, TypeError)):
            provider.origin = "https://example.invalid"

    def test_rejects_unsafe_production_origins_and_categories(self):
        invalid_origins = (
            "",
            "http://racetime.gg",
            "https://racetime.gg/path",
            "https://racetime.gg?query=1",
            "https://racetime.gg#fragment",
            "https://user@racetime.gg",
            "https://user:password@racetime.gg",
            "https://127.0.0.1",
            "https://[::1]",
            "https://racetime.gg.",
            "not an origin",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin), self.assertRaises(ProviderConfigurationError):
                RacetimeProvider(origin=origin, category="z1rr")

        for category in ("", "Z1RR", "z1rr/path", "z1rr_", "-z1rr", "z1rr%2fother"):
            with self.subTest(category=category), self.assertRaises(ProviderConfigurationError):
                RacetimeProvider(origin="https://racetime.gg", category=category)

    def test_http_loopback_is_explicit_and_local_only(self):
        for origin in ("http://localhost:8080/", "http://127.0.0.1:8080"):
            with self.subTest(origin=origin):
                provider = RacetimeProvider(
                    origin=origin,
                    category="z1rr",
                    allow_insecure_loopback=True,
                )
                self.assertFalse(provider.secure)
        with self.assertRaises(ProviderConfigurationError):
            RacetimeProvider(
                origin="http://localhost:8080",
                category="z1rr",
            )
        with self.assertRaises(ProviderConfigurationError):
            RacetimeProvider(
                origin="http://example.com",
                category="z1rr",
                allow_insecure_loopback=True,
            )

    def test_resolves_http_and_location_only_on_exact_origin(self):
        provider = RacetimeProvider(
            origin="https://racetime.z1rracing.com",
            category="z1rr",
        )
        self.assertEqual(
            provider.http_url("/o/z1rr/startrace"),
            "https://racetime.z1rracing.com/o/z1rr/startrace",
        )
        self.assertEqual(
            provider.resolve_location("/z1rr/example-room"),
            "https://racetime.z1rracing.com/z1rr/example-room",
        )
        self.assertEqual(
            provider.resolve_location(
                "https://racetime.z1rracing.com/z1rr/example-room"
            ),
            "https://racetime.z1rracing.com/z1rr/example-room",
        )
        for location in (
            "",
            "//evil.example/z1rr/room",
            "https://racetime.gg/z1rr/room",
            "/z1rr%2fother/room",
            "/z1rr/../other",
            "/other/room",
        ):
            with self.subTest(location=location), self.assertRaises(ProviderConfigurationError):
                provider.resolve_location(location)

    def test_websocket_resolution_uses_provider_transport(self):
        secure = RacetimeProvider("https://racetime.gg", "z1rr")
        self.assertEqual(
            secure.websocket_url("/ws/z1rr/example-room"),
            "wss://racetime.gg/ws/z1rr/example-room",
        )
        self.assertEqual(
            secure.websocket_url("wss://racetime.gg/ws/z1rr/example-room"),
            "wss://racetime.gg/ws/z1rr/example-room",
        )
        with self.assertRaises(ProviderConfigurationError):
            secure.websocket_url("wss://evil.example/ws/z1rr/example-room")
        insecure = RacetimeProvider(
            "http://localhost:8080", "z1rr", allow_insecure_loopback=True
        )
        self.assertEqual(
            insecure.websocket_url("/ws/z1rr/room"),
            "ws://localhost:8080/ws/z1rr/room",
        )


if __name__ == "__main__":
    unittest.main()
