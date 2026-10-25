"""HTTPS calls verify servers against the certifi bundle, not the old system
store of the Pi, which lacks the root cdn.fmi.fi chains to."""

import re
from pathlib import Path
from unittest.mock import patch

from src.common.trusted_http import SSL_CONTEXT, trusted_session

SRC = Path(__file__).parents[2] / "src"


def _common_names():
    for cert in SSL_CONTEXT.get_ca_certs():
        for rdn in cert["subject"]:
            for key, value in rdn:
                if key == "commonName":
                    yield value


def test_the_context_trusts_the_root_of_cdn_fmi_fi():
    assert "HARICA TLS RSA Root CA 2021" in set(_common_names())


def test_no_module_opens_a_session_of_its_own():
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path.name != "trusted_http.py"
        and re.search(r"\bClientSession\(|aiohttp\.request\(", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_a_trusted_session_verifies_with_the_certifi_context():
    with patch("src.common.trusted_http.aiohttp") as aiohttp:
        session = trusted_session(raise_for_status=True)
    aiohttp.TCPConnector.assert_called_once_with(ssl=SSL_CONTEXT)
    aiohttp.ClientSession.assert_called_once_with(
        connector=aiohttp.TCPConnector.return_value, raise_for_status=True
    )
    assert session is aiohttp.ClientSession.return_value
