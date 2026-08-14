"""Opaque per-plot QR locator (round 20 QR hardening).

Generation only — no hash/verify pair like app/services/inspection_code.py,
because this value is looked up by exact match (see
plot_repository.get_plot_by_qr_key), not compared like a password. See
migration 0026_plots_qr_key's docstring for why the column stores this in
plaintext rather than a hash.
"""
from __future__ import annotations

import secrets

_QR_KEY_BYTES = 24  # secrets.token_urlsafe output is ~1.3x this in chars; 192 bits of entropy


def generate_qr_key() -> str:
    return secrets.token_urlsafe(_QR_KEY_BYTES)
