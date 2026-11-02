"""Use explicit application proxy settings, not the launching shell's proxies."""
import httpx
from .config import GROQ_PROXY_URL


def groq_http_client():
    try:
        return httpx.AsyncClient(trust_env=False, proxy=GROQ_PROXY_URL or None, verify=True)
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Invalid GROQ_PROXY_URL. Use an HTTP or HTTPS proxy URL, or leave it empty for a direct connection.") from None


def connection_failure_kind(error):
    for error_type, label in (
        (httpx.ProxyError, "proxy_error"),
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.ConnectError, "connect_error"),
    ):
        if isinstance(error, error_type):
            return label
    return "transport_error"
