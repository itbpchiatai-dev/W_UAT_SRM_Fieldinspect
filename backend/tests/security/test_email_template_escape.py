"""HIGH-4 regression — every user/admin-controlled value that gets
interpolated into the HTML email body must be html-escaped before
landing in the output. We assert presence-of-entity AND absence-of-
raw-tag so a future refactor that swaps the escape order doesn\'t pass.

Attack payload: classic stored-XSS string. If escape regresses we\'d
see literal <img src=x onerror=alert(1)> in the rendered email body —
Outlook/Gmail wouldn\'t fire it, but a browser-based webmail might.
"""
from __future__ import annotations

from app.services.notifications.templates import (
    approval_user, rejection_user, signup_admin,
)

XSS = "<img src=x onerror=alert(1)>"
SCRIPT = "<script>alert(1)</script>"


def test_signup_admin_escapes_user_name() -> None:
    _, body = signup_admin(
        user_email="ok@example.com",
        user_name=XSS,
        app_name="App",
        approve_url="https://example/a", reject_url="https://example/r",
    )
    assert XSS not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_signup_admin_escapes_user_email() -> None:
    _, body = signup_admin(
        user_email='evil@x"><script>alert(1)</script>',
        user_name="Mallory",
        app_name="App",
        approve_url="https://example/a", reject_url="https://example/r",
    )
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_signup_admin_escapes_app_name() -> None:
    _, body = signup_admin(
        user_email="ok@example.com", user_name="Alice",
        app_name=SCRIPT,
        approve_url="https://example/a", reject_url="https://example/r",
    )
    assert SCRIPT not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_approval_user_escapes_admin_message() -> None:
    _, body = approval_user(
        user_name="Alice",
        login_url="https://example/login",
        message=f"Welcome {XSS}",
    )
    assert XSS not in body
    assert "&lt;img" in body


def test_rejection_user_escapes_reason() -> None:
    _, body = rejection_user(
        user_name="Alice", reason=XSS, message=None,
    )
    assert XSS not in body
    assert "&lt;img" in body


def test_rejection_user_escapes_admin_message() -> None:
    _, body = rejection_user(
        user_name="Alice", reason="too bad", message=SCRIPT,
    )
    # `message` is escaped before its newlines become <br> — see _esc().
    assert SCRIPT not in body
    assert "&lt;script&gt;" in body
