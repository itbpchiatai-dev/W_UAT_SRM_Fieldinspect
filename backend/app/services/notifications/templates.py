"""Email body templates — bilingual (TH stacked over EN).

We send ONE message containing both languages stacked with a divider so
recipients in either locale get readable content without us needing a
per-user language preference column. Admin\'s edited message stays
single-language (whatever they typed on the approval page); the rest of
the body — headers, footer, button labels — is bilingual.

Kept as Python f-strings rather than Jinja for now — three templates
each is small enough to not warrant a templating engine. Add real Jinja
under app/services/notifications/templates/*.html when this grows past
~5 templates.

Closes Deep-Audit HIGH-4 — every interpolated value that originates
from a user (name, email, admin\'s reply text, rejection reason, app
name from settings) is run through html.escape() before being placed
inside the HTML body. Static template markup (brand colors, layout
divs) is intentionally NOT escaped — it\'s author-controlled.

URLs (`approve_url`, `reject_url`, `login_url`) are NOT html-escaped
inside href attributes — html.escape would mangle the `&` separator in
real query strings. They are server-generated from settings
(`AUTH_FRONTEND_BASE_URL`) + a server-generated approval token, so
they\'re not an injection vector. If a future change accepts a
user-controlled URL fragment, escape that fragment BEFORE building the
URL.
"""
from __future__ import annotations

import html


def _esc(value: str | None) -> str:
    """HTML-escape a single user-controlled string for inline body use.

    None / empty → empty string (callers display their own fallback).
    quote=True so values that ever land inside attribute context (e.g.
    a future <span title="..."> usage) are also safe.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


_CARD_OPEN = (
    "<html><body style=\"font-family: Tahoma, \'Leelawadee UI\', sans-serif; "
    "color:#1f2937; background:#e2e5e5; padding:24px 0; margin:0;\">"
    "<div style=\"max-width:560px; margin:0 auto; background:#fff; "
    "border-top:4px solid #B29530; border-radius:6px; padding:24px 28px;\">"
)
_CARD_FOOTER = (
    "<p style=\"font-size:10pt;color:#b4b8c0;margin-top:24px;"
    "border-top:1px solid #e2e5e5;padding-top:12px;\">"
    "อีเมลนี้ส่งโดยระบบอัตโนมัติ — ไม่ต้องตอบกลับ"
    "<br>This is an automated email — please do not reply."
    "</p></div></body></html>"
)
# Visual divider between Thai and English sections inside the card.
_LANG_DIVIDER = (
    "<div style=\"margin:18px 0; text-align:center; color:#b4b8c0; font-size:10pt;\">"
    "<span style=\"display:inline-block; width:30%; border-top:1px solid #e2e5e5; "
    "vertical-align:middle; margin-right:8px;\"></span>"
    "ENGLISH"
    "<span style=\"display:inline-block; width:30%; border-top:1px solid #e2e5e5; "
    "vertical-align:middle; margin-left:8px;\"></span>"
    "</div>"
)


def signup_admin(*, user_email: str, user_name: str,
                 app_name: str,
                 approve_url: str, reject_url: str) -> tuple[str, str]:
    """Notification → admin. Bilingual: Thai section then ENGLISH divider
    then English section. Both buttons point to the same one-time tokens
    so it doesn\'t matter which language section admin reads from.

    Brand palette (CT) — green #114B33 + gold #B29530 + warm callout.
    Inline styles only; many mail clients strip <style> blocks.

    All user-controlled values (user_name, user_email, app_name) are
    html.escape()d before interpolation — closes Deep-Audit HIGH-4.
    URLs are NOT escaped (mangles real `&` in query strings) — they\'re
    server-generated, not a user-controlled vector.
    """
    subject = "ขออนุมัติการเข้าใช้งาน · Access Request Approval"
    safe_name_th = _esc(user_name) if user_name else "(ไม่ระบุชื่อ)"
    safe_name_en = _esc(user_name) if user_name else "(name not provided)"
    safe_email = _esc(user_email)
    safe_app = _esc(app_name)
    buttons = (
        f'<p style="margin:18px 0 12px 0;">'
        f'<a href="{approve_url}" style="background:#114B33;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;margin-right:8px;">✓ อนุมัติ / Approve</a>'
        f'<a href="{reject_url}" style="background:#dc2626;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;">✕ ปฏิเสธ / Reject</a>'
        f'</p>'
    )
    body = f"""
    {_CARD_OPEN}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">ขออนุมัติการเข้าใช้งาน</h2>
      <p style="margin:6px 0;"><b>{safe_name_th}</b> ({safe_email})<br>
         ต้องการเข้าใช้งาน <b>{safe_app}</b></p>
      <p style="font-size:10pt;color:#114B33;background:#f9e6bc;padding:8px 12px;border-radius:4px;margin:12px 0;">
        ลิงก์ใช้ได้ครั้งเดียว — ถ้ามีผู้ดูแลท่านอื่นดำเนินการไปก่อน ลิงก์จะหมดอายุอัตโนมัติ
      </p>
      {_LANG_DIVIDER}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">Access Request Approval</h2>
      <p style="margin:6px 0;"><b>{safe_name_en}</b> ({safe_email})<br>
         is requesting access to <b>{safe_app}</b>.</p>
      <p style="font-size:10pt;color:#114B33;background:#f9e6bc;padding:8px 12px;border-radius:4px;margin:12px 0;">
        Single-use link — it expires automatically if another administrator acts first.
      </p>
      {buttons}
      {_CARD_FOOTER}
    """
    return subject, body


# Back-compat alias for callers that still import the _th name.
signup_admin_th = signup_admin


def approval_user_default_message_th(*, user_name: str) -> str:
    """Plain-text default message body shown in admin\'s editable textarea
    on the approval page when locale=th."""
    return (
        f"เรียน {user_name or 'ผู้ใช้'},\n\n"
        f"ผู้ดูแลระบบได้อนุมัติบัญชีของคุณเรียบร้อย "
        f"คุณสามารถเข้าใช้งานระบบได้แล้ว"
    )


def approval_user_default_message_en(*, user_name: str) -> str:
    """Plain-text default message body when locale=en."""
    return (
        f"Dear {user_name or 'user'},\n\n"
        f"Your account has been approved by the administrator. "
        f"You may now sign in to the system."
    )


def approval_user(*, user_name: str, login_url: str,
                  message: str | None = None) -> tuple[str, str]:
    """Reply → user: approved. Bilingual stacked.

    `message` is admin\'s edited copy from the public approve page (one
    language — whichever they typed). Shown verbatim in the Thai section;
    English section gets the canonical English default message. If
    `message` is empty, both sections show their respective defaults.

    `message` is admin-controlled but still untrusted (admin accounts can
    be phished or compromised). Escape it before converting "\n" → <br>.
    """
    subject = "บัญชีของคุณได้รับการอนุมัติแล้ว · Account approved"
    th_text = (message or approval_user_default_message_th(user_name=user_name)).strip()
    en_text = approval_user_default_message_en(user_name=user_name).strip()
    # Escape FIRST, then turn newlines into <br>. The other way round
    # would leak any <br> the admin had literally typed AND still leave
    # actual angle-brackets unsafe.
    th_html = _esc(th_text).replace("\n", "<br>")
    en_html = _esc(en_text).replace("\n", "<br>")
    body = f"""
    {_CARD_OPEN}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">บัญชีของคุณได้รับการอนุมัติแล้ว</h2>
      <p style="margin:12px 0;">{th_html}</p>
      <p style="margin:20px 0;"><a href="{login_url}" style="background:#114B33;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;">เข้าสู่ระบบ</a></p>
      {_LANG_DIVIDER}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">Account approved</h2>
      <p style="margin:12px 0;">{en_html}</p>
      <p style="margin:20px 0;"><a href="{login_url}" style="background:#114B33;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;">Sign in</a></p>
      {_CARD_FOOTER}
    """
    return subject, body


approval_user_th = approval_user  # back-compat alias


def rejection_user_default_message_th(*, user_name: str) -> str:
    return (
        f"เรียน {user_name or 'ผู้ใช้'},\n\n"
        f"ขออภัย คำขอเข้าใช้งานของคุณยังไม่ได้รับการอนุมัติในขณะนี้ "
        f"หากต้องการสอบถามเพิ่มเติม กรุณาติดต่อผู้ดูแลระบบ"
    )


def rejection_user_default_message_en(*, user_name: str) -> str:
    return (
        f"Dear {user_name or 'user'},\n\n"
        f"We regret to inform you that your access request has not been approved "
        f"at this time. Please contact the administrator if you have further questions."
    )


def rejection_user(*, user_name: str, reason: str | None,
                   message: str | None = None) -> tuple[str, str]:
    """Reply → user: rejected. Bilingual stacked.

    `message` is admin\'s edited copy (single language) — shown in the
    Thai section. English section shows the canonical English default.
    `reason` (optional) is the short structured cause and appears on both
    sides, since it\'s a noun phrase the admin typed.

    Both `message` and `reason` are admin-typed and trusted-but-untrusted.
    Closes Deep-Audit HIGH-4 — escape both before HTML interpolation.
    """
    subject = "คำขอเข้าใช้งานของคุณ · Your access request"
    th_text = (message or rejection_user_default_message_th(user_name=user_name)).strip()
    en_text = rejection_user_default_message_en(user_name=user_name).strip()
    th_html = _esc(th_text).replace("\n", "<br>")
    en_html = _esc(en_text).replace("\n", "<br>")
    reason_block_th = ""
    reason_block_en = ""
    if reason:
        safe_reason = _esc(reason)
        reason_block_th = (
            f'<p style="background:#fef2f2;border-left:3px solid #dc2626;'
            f'padding:8px 12px;color:#7f1d1d;font-size:11pt;margin:12px 0;">'
            f'<b>เหตุผล:</b><br>{safe_reason}</p>'
        )
        reason_block_en = (
            f'<p style="background:#fef2f2;border-left:3px solid #dc2626;'
            f'padding:8px 12px;color:#7f1d1d;font-size:11pt;margin:12px 0;">'
            f'<b>Reason:</b><br>{safe_reason}</p>'
        )
    body = f"""
    {_CARD_OPEN}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">คำขอเข้าใช้งานของคุณ</h2>
      <p style="margin:12px 0;">{th_html}</p>
      {reason_block_th}
      {_LANG_DIVIDER}
      <h2 style="color:#114B33; font-size:18pt; margin:0 0 12px 0;">Your access request</h2>
      <p style="margin:12px 0;">{en_html}</p>
      {reason_block_en}
      {_CARD_FOOTER}
    """
    return subject, body


rejection_user_th = rejection_user  # back-compat alias
