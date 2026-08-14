"""SSRF guard regression — app/core/safe_url.assert_safe_url must block
internal / metadata / non-http targets and accept public ones. Uses literal
IPs so the test never depends on DNS or the network."""
from __future__ import annotations

import pytest

from app.core.safe_url import UnsafeURLError, assert_safe_url, is_safe_url

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata (the big one)
    "http://127.0.0.1:8000/admin",               # loopback
    "http://10.0.0.5/internal",                  # RFC1918 private
    "http://192.168.1.1/",                       # RFC1918 private
    "http://[::1]/",                             # IPv6 loopback
    "ftp://example.com/file",                    # disallowed scheme
    "file:///etc/passwd",                        # disallowed scheme
    "javascript:alert(1)",                       # disallowed scheme
    "",                                          # empty
    "http://",                                   # no host
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocks_unsafe(url: str) -> None:
    assert is_safe_url(url) is False
    with pytest.raises(UnsafeURLError):
        assert_safe_url(url)


def test_allows_public_literal_ip() -> None:
    # 8.8.8.8 / 1.1.1.1 are globally routable; literal-IP path avoids DNS.
    assert assert_safe_url("http://8.8.8.8/path") == "http://8.8.8.8/path"
    assert is_safe_url("https://1.1.1.1/") is True


def test_allowlist_rejects_unlisted_public_host() -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url("http://8.8.8.8/", allowed_hosts={"1.1.1.1"})
    # A listed host passes even with the allowlist active.
    assert assert_safe_url("http://8.8.8.8/", allowed_hosts={"8.8.8.8"})


def test_ipv4_mapped_ipv6_metadata_is_blocked() -> None:
    # ::ffff:169.254.169.254 must unwrap to the link-local v4 and be blocked.
    assert is_safe_url("http://[::ffff:169.254.169.254]/") is False
