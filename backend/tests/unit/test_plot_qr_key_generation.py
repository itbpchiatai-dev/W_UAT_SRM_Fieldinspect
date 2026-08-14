"""app.services.plot_qr_key.generate_qr_key — opaque per-plot QR locator
(round 20 QR hardening)."""
from __future__ import annotations

import re

from app.services.plot_qr_key import generate_qr_key


def test_generate_qr_key_returns_a_reasonably_long_string() -> None:
    key = generate_qr_key()
    assert isinstance(key, str)
    # secrets.token_urlsafe(24) -> ~32 chars; comfortably unguessable, well
    # under the 64-char column limit (migration 0026_plots_qr_key).
    assert len(key) >= 30


def test_generate_qr_key_is_url_safe_with_no_padding() -> None:
    key = generate_qr_key()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", key)


def test_generate_qr_key_is_random_each_call() -> None:
    keys = {generate_qr_key() for _ in range(200)}
    assert len(keys) == 200
