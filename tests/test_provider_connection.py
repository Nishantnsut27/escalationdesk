import os
import unittest
from unittest.mock import patch
import httpx
import httpcore
from backend.provider import groq_http_client, connection_failure_kind


class ProviderConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inherited_proxy_cannot_redirect_provider_but_explicit_proxy_works(self):
        # No network request: inspect the transport selected with poisoned shell
        # settings, then an intentionally configured application proxy.
        poisoned = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                    "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}
        with patch.dict(os.environ, poisoned), patch("backend.provider.GROQ_PROXY_URL", ""):
            async with groq_http_client() as client:
                transport = client._transport_for_url(httpx.URL("https://api.groq.com/openai/v1/models"))
                self.assertNotIsInstance(transport._pool, httpcore.AsyncHTTPProxy)
                self.assertTrue(transport._pool._ssl_context.check_hostname)
        with patch.dict(os.environ, poisoned), patch("backend.provider.GROQ_PROXY_URL", "http://proxy.example.test:8080"):
            async with groq_http_client() as client:
                transport = client._transport_for_url(httpx.URL("https://api.groq.com/openai/v1/models"))
                self.assertIsInstance(transport._pool, httpcore.AsyncHTTPProxy)
                self.assertEqual(transport._pool._proxy_url.host, b"proxy.example.test")

    async def test_bad_proxy_configuration_does_not_echo_credential(self):
        with patch("backend.provider.GROQ_PROXY_URL", "ftp://user:PRIVATE_PROXY_SECRET@proxy.example.test"):
            with self.assertRaises(ValueError) as caught:
                groq_http_client()
        self.assertNotIn("PRIVATE_PROXY_SECRET", str(caught.exception))
        self.assertIn("GROQ_PROXY_URL", str(caught.exception))

    def test_transport_diagnostics_are_safe_categories(self):
        for error, label in ((httpx.ProxyError("SECRET"), "proxy_error"),
                             (httpx.ConnectTimeout("SECRET"), "connect_timeout"),
                             (httpx.ReadTimeout("SECRET"), "read_timeout"),
                             (httpx.ConnectError("SECRET"), "connect_error"),
                             (httpx.RemoteProtocolError("SECRET"), "transport_error")):
            self.assertEqual(connection_failure_kind(error), label)
