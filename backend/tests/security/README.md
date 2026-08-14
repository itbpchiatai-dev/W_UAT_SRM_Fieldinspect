# Security regression tests

These tests guard the HIGH findings closed in the v3.0.6 audit rounds.
They run as part of `pytest tests/security/` and are deliberately
DB-free unit tests — fast enough to live on every commit so a refactor
that accidentally regresses one of the fixes blows up at CI time, not
in production. `conftest.py` sets the env vars Settings expects, so
test files can import app modules without per-file boilerplate.

| File | Audit ref | What it locks down |
|------|-----------|--------------------|
| `test_rate_limit_wiring.py`             | Round 3 HIGH-1 | `/login`, `/sso/callback`, `/refresh`, approval-token endpoints carry `@limiter.limit` |
| `test_users_approve_permission.py`      | Round 3 HIGH-2 | `users.approve` seeds + only admin roles hold it by default |
| `test_patch_user_self_guard.py`         | Round 3 HIGH-2 | `patch_user`/`bulk_approve`/`deactivate_user` cannot be turned on the caller |
| `test_jwt_jti.py`                       | Round 3 HIGH-3 | Refresh tokens carry a unique `jti` claim |
| `test_email_template_escape.py`         | Round 3 HIGH-4 | `signup_admin`/`approval_user`/`rejection_user` HTML-escape user input |
| `test_jwt_secret_validation.py`         | Round 4 HIGH-1 | JWT_SECRET_KEY mandatory + len>=32 + placeholder/entropy rejection |
| `test_rate_limit_production_storage.py` | Round 4 HIGH-2 + Round 5 HIGH-1 | Production blocks `memory://`; XFF walked **right-to-left** skipping trusted proxies (attacker prepend bypass blocked) |
| `test_safe_redirect.py`                 | Round 4 HIGH-3 | `lib/safe-redirect.ts` blocks `//`, `javascript:`, `\\`, etc.; Login + RequireAuth use it |
| `test_add_override_self_guard.py`       | Round 4 HIGH-4 + Round 5 HIGH-2 | `add_override` blocks self + full deny-list (`users.approve`, `admin_settings.*`, `users.delete`, `roles.*`, `permissions.*`) for non-super_admin |

Integration-style tests (full HTTP round-trip with TestClient + a real
sqlite DB) belong under `tests/integration/auth/` — add them as the
project grows.
