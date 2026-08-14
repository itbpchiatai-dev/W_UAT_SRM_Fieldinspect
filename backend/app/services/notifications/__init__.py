"""Notifications subsystem — re-export the public API.

`from app.services.notifications import notify_*` is the supported
shape; the underlying dispatcher module is an implementation detail.
"""
from app.services.notifications.dispatcher import (
    notify_admin_new_signup,
    notify_user_approval_granted,
    notify_user_rejection,
)

__all__ = [
    "notify_admin_new_signup",
    "notify_user_approval_granted",
    "notify_user_rejection",
]
