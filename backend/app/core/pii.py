"""PII / secret masking utilities.

Used by StructuredLogger (app.core.logging), ActivityLogger
(app.services.loggers.activity_logger), AiCallLogger
(app.services.loggers.ai_call_logger) and SystemLogger to redact PII
**and known secret shapes** before persisting or emitting log records.

Why secrets too: AI prompts / exception messages frequently echo back
tokens, API keys, and bearer headers that a generic email/phone regex
would miss. Keeping the catalog here means one allow-list per project.

See docs/logging.md §4 and AGENTS.md §3.
"""
from __future__ import annotations

import re

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b0[0-9]{8,9}\b|\b\+?66[0-9]{8,9}\b")
THAI_ID_PATTERN = re.compile(r"\b[0-9]{13}\b")

# Secret shapes — must stay in sync with scripts/checks/no_real_secrets_in_examples.py
# Kept narrow on purpose: each pattern is anchored to a known prefix so we
# don\'t mangle ordinary alphanumeric text in user prompts.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Anthropic / OpenAI keys: sk-ant-..., sk-proj-..., sk-<alnum>
    re.compile(r"sk-(?:ant|proj)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    # JWT (header.payload.signature, base64url)
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    # GitHub tokens
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}"),
    # AWS access key IDs
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    # Slack tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
)


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"**@{domain}"
    return f"{local[:2]}***@{domain}"


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_pii_in_text(text: str) -> str:
    """Mask PII + known secret prefixes in free-form text.

    Applies, in order: emails, Thai phones, Thai national IDs, then a
    catalog of secret shapes (API keys / JWT / GitHub tokens / etc.).
    See module docstring for why secrets are masked here too.
    """
    text = EMAIL_PATTERN.sub(lambda m: mask_email(m.group(0)), text)
    text = PHONE_PATTERN.sub(lambda m: mask_phone(m.group(0)), text)
    text = THAI_ID_PATTERN.sub("***-****-****-**", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text
