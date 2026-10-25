"""HTTPS sessions that trust the certifi bundle. The system store of the Pi
(Debian 11) lacks newer roots, such as the one cdn.fmi.fi chains to."""

import ssl

import aiohttp
import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def trusted_session(**kwargs) -> aiohttp.ClientSession:
    """An aiohttp session that verifies servers against the certifi bundle."""
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CONTEXT), **kwargs)
