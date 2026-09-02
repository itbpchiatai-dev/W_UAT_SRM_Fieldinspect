import ipaddress
from functools import lru_cache
from typing import ClassVar

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Round 8-16D.1 — the closed set of environments this repo actually runs.
# Enumerated from real usage, not invented:
#   dev         backend/.env, docker-compose.dev.yml, prodlike
#   test        .github/workflows/ci.yml (pytest job)
#   smoke       docker-compose.smoke.yml + scripts/smoke-prod.{sh,bat}
#               + the ci.yml `docker-smoke` job
#   staging     reserved by the deployment contract (no live user yet)
#   production  backend/.env.prod.example, docker-compose.yml
#
# Anything else — "prod", "Production", "", "PRODUCTION" — is a typo and
# must fail at boot. It is NEVER normalised to "production": silently
# accepting "prod" would mean a deploy that *looks* production-gated while
# every production guard sits inactive, which is the failure this closed
# set exists to make impossible.
APP_ENV_DEV = "dev"
APP_ENV_TEST = "test"
APP_ENV_SMOKE = "smoke"
APP_ENV_STAGING = "staging"
APP_ENV_PRODUCTION = "production"
SUPPORTED_APP_ENVS: frozenset[str] = frozenset({
    APP_ENV_DEV, APP_ENV_TEST, APP_ENV_SMOKE, APP_ENV_STAGING, APP_ENV_PRODUCTION,
})


def _is_valid_cidr(value: str) -> bool:
    """True when `value` is an IP or CIDR that app.core.rate_limit will
    actually keep. Mirrors that module's `ip_network(..., strict=False)`
    call so the boot-time check and the runtime parser cannot disagree."""
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


class SettingsConfigError(RuntimeError):
    """Base for every boot-time settings failure in this module.

    **Why none of these are ValueError.** Pydantic converts a ValueError
    raised inside a validator into a ValidationError whose rendered message
    embeds `input_value=<the raw settings dict>`. That dict is the
    PRE-construction input, so `SecretStr` fields are still plaintext in it.
    Pydantic truncates the middle of that repr but keeps the head AND tail,
    so whether a given secret is exposed depends only on where it happens to
    fall in the dict — round 8-16D.1 reproduced live leaks of
    PLOT_ACCESS_PASSWORD_PEPPER, DB_PASSWORD and SMTP_PASSWORD this way.
    A boot failure is exactly when the traceback gets pasted into a ticket
    or shipped to a log aggregator, so "usually truncated" is not a control.

    Raising a non-ValueError makes pydantic propagate the exception
    untouched: the message below is all the caller ever sees, and every
    message in this module names SETTINGS and remedies, never values.

    Do not catch these and re-raise as ValueError/ValidationError — that
    reintroduces the echo this hierarchy exists to prevent.
    """


class MissingRequiredSettingsError(SettingsConfigError):
    """A required secret setting is absent, empty, or whitespace-only.

    Round 8-16D.2 closed the last echo path left by 8-16D.1. DB_PASSWORD and
    JWT_SECRET_KEY are declared without defaults, so when one is missing
    pydantic fails during FIELD parsing — before any mode="after" validator
    can run — and raises a ValidationError of its own.

    Round 8-16D.1 checked only `str(exc)`, which pydantic truncates, and so
    reported that path as clean. It is not: `exc.errors()` carries the
    untruncated `input` mapping, and a live reproduction showed
    DB_PASSWORD, SMTP_PASSWORD, PLOT_ACCESS_PASSWORD_PEPPER and
    AUTH_MFA_ENCRYPTION_KEY all present in the structured form while the
    rendered string showed none of them. The structured form is what error
    trackers and log shippers serialise, so it is the one that matters.

    The fix is a mode="before" validator (see _required_settings_present):
    it runs ahead of field parsing and raises this instead, so pydantic
    never constructs a ValidationError and there is no `input` to carry.
    """


class AppEnvConfigError(SettingsConfigError):
    """APP_ENV is not one of the supported environments (round 8-16D.1)."""


class CorsConfigError(SettingsConfigError):
    """API_CORS_ORIGINS is unusable."""


class JwtSecretConfigError(SettingsConfigError):
    """JWT_SECRET_KEY is missing, weak, or a placeholder."""


class RateLimitStorageConfigError(SettingsConfigError):
    """RATE_LIMIT_STORAGE_URI is unusable for the selected APP_ENV."""


class ProductionConfigError(SettingsConfigError):
    """APP_ENV=production preflight failed — raised at boot (round 8-16D)."""


class PlotAccessPepperConfigError(SettingsConfigError):
    """PLOT_ACCESS_PASSWORD_PEPPER is unusable — raised at boot (round 8-9A.1).

    Deliberately NOT a ValueError. Pydantic converts a ValueError raised inside
    a validator into a ValidationError whose `input` echoes the ENTIRE raw
    settings dict — i.e. every env-sourced secret in plaintext, the pepper
    included — into whatever renders the traceback. SecretStr masks the FIELD
    on the constructed model, but the error carries the pre-construction input,
    so masking alone cannot close this. A non-ValueError propagates untouched,
    carrying only the message below (which names settings, never values).
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=True, extra="ignore",
    )
    APP_NAME: str = "srm-fieldinspect"
    APP_ENV: str = "dev"
    APP_DEBUG: bool = False
    APP_LOG_LEVEL: str = "INFO"
    API_CORS_ORIGINS: str = "http://localhost:5173"
    # เก็บเป็น str แล้ว parse ใน property เพื่อหลีกเลี่ยง pydantic parse error
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_NAME: str = "srm_fieldinspect"
    DB_USER: str = "srm_fieldinspect"
    # No default — env var must provide DB_PASSWORD (audit finding #1).
    # Round 8-16D.2: a missing OR blank/whitespace-only value is intercepted
    # by _required_settings_present (mode="before") and raises
    # MissingRequiredSettingsError. It no longer reaches pydantic's own
    # required-field check, so no ValidationError carrying the raw settings
    # mapping is ever constructed for this field.
    DB_PASSWORD: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    # Step 8: limited runtime role for RLS enforcement.
    # When set, the async engine pool connects as this role (no BYPASSRLS).
    # Alembic/seed continue to use DB_USER (owner). Empty = use DB_USER.
    DB_APP_USER: str = ""
    DB_APP_PASSWORD: str = ""
    # Database Connections module — opt-in. When false (default) the router
    # is not mounted and seed.py skips its permissions / menus / settings.
    FEATURE_DB_CONNECTIONS: bool = False
    # Fernet key — required only when FEATURE_DB_CONNECTIONS=true.
    DB_CONNECTIONS_ENCRYPTION_KEY: str = ""
    AZURE_AD_TENANT_ID: str = ""
    AZURE_AD_CLIENT_ID: str = ""
    AZURE_AD_CLIENT_SECRET: str = ""
    # No default — env var must provide JWT_SECRET_KEY (Round-4 HIGH-1).
    # The model_validator below additionally enforces length>=32 and
    # rejects known placeholder values. init_project.py generates a
    # 64-char hex value on first scaffold, so the new-project flow stays
    # zero-config; only manual .env weakening will fail.
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Auth module hooks (consumed only if `modules/auth` is installed).
    # Kept here so the Settings surface is stable across module install/uninstall.
    AZURE_AD_REDIRECT_URI: str = ""
    AUTH_MFA_ENCRYPTION_KEY: str = ""
    AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL: str = ""
    AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE: str = "sso"
    # AUTH_FRONTEND_BASE_URL — public SPA origin used by the auth module to
    # generate absolute password-reset / invitation links in email. Empty
    # makes auth code fall back to relative paths. Will be renamed to
    # FRONTEND_BASE_URL during the auth-absorption sprint (auth content
    # moves from ct-web-modules into this scaffold).
    AUTH_FRONTEND_BASE_URL: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_TLS: bool = True
    # Microsoft 365 Graph email — preferred outbound channel for CT apps.
    # When all four are set + app_settings.notifications.email.enabled=true,
    # the notification service routes through Graph instead of SMTP.
    M365_TENANT_ID: str = ""
    M365_CLIENT_ID: str = ""
    M365_CLIENT_SECRET: str = ""
    M365_SENDER_EMAIL: str = ""
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    RATE_LIMIT_PER_MINUTE: int = 60
    # Round-4 HIGH-2: production rate limiting must use shared storage.
    # `memory://` is per-process — useless when --workers > 1 OR when
    # running multiple replicas. The model_validator below blocks
    # APP_ENV=production + memory:// at boot. For production set
    # `redis://<host>:6379/0` (slowapi reads it via the limits package).
    RATE_LIMIT_STORAGE_URI: str = "memory://"
    # Comma-separated CIDR list of proxy IPs whose X-Forwarded-For we
    # trust to identify the real client. Empty list = NO header trust
    # (request.client.host is used directly). When fronted by nginx in
    # the docker-compose proxy-net, set this to the nginx container's
    # subnet (e.g. "10.0.0.0/8" for the docker default bridge). NEVER
    # set this for a public-internet-facing service without a real proxy.
    TRUSTED_PROXY_IPS: str = ""
    # CT App Registry — ดู docs/ops/registry.md §2
    REGISTRY_URL: str = ""
    REGISTRY_API_KEY: str = ""
    PROJECT_SLUG: str = "srm-fieldinspect"
    # Round 13 — local/dev inspection-photo storage. Relative path resolves
    # against the backend process's cwd (same convention as alembic.ini).
    # No route serves INSPECTION_PHOTOS_URL_PREFIX yet (see
    # app/services/inspection_photos.py docstring) — it's stored now so the
    # eventual serving/download endpoint doesn't require a data migration.
    INSPECTION_PHOTOS_DIR: str = "var/inspection-photos"
    INSPECTION_PHOTOS_URL_PREFIX: str = "/media/inspection-photos"
    # Round 8-16B — Huawei OBS (S3-compatible) photo storage.
    # When OBS_ENDPOINT + OBS_ACCESS_KEY_ID are set, get_photo_storage()
    # returns OBSPhotoStorage instead of LocalPhotoStorage. Objects are stored
    # at {OBS_ENV_PREFIX}/{plot_code}/{uuid}.webp inside OBS_BUCKET_NAME.
    # Presigned URLs (expiry: OBS_PRESIGNED_EXPIRY_SECONDS) are used for
    # the download route — objects remain private; no public-read ACL required.
    OBS_ENDPOINT: str = ""           # e.g. "obs.ap-southeast-2.myhuaweicloud.com"
    OBS_ACCESS_KEY_ID: str = ""
    OBS_SECRET_ACCESS_KEY: str = ""
    OBS_BUCKET_NAME: str = ""
    OBS_ENV_PREFIX: str = "UAT"      # "PROD" or "UAT"
    OBS_PRESIGNED_EXPIRY_SECONDS: int = 900  # 15 min
    # Explicit connect+read timeout for every OBS SDK call (put/delete/sign).
    # The SDK's own ObsClient default (60s) is unbounded enough to hold a
    # request handler open uncomfortably long on a network hiccup; tightened
    # here rather than left implicit so it shows up as a real, documented
    # config knob instead of "whatever this SDK version happens to default
    # to."
    OBS_TIMEOUT_SECONDS: int = 30
    # Round 8-9A — per-Plot inspection password ("รหัสยืนยันแปลง").
    # PLOT_ACCESS_PASSWORD_PEPPER is the DEDICATED server-side key for the
    # HMAC-SHA256 blind index (app/auth/plot_access_password.py). It is NOT
    # interchangeable with JWT_SECRET_KEY — the validator below rejects reuse,
    # so token forgery and credential lookup never share a blast radius.
    # Empty = no digest can be built; admin credential mutations answer a
    # controlled 503 rather than falling back to another secret.
    #
    # Round 8-9A.1: SecretStr, not str. Every incidental serialization of
    # Settings — repr() in a traceback frame, model_dump_json() in a debug
    # dump, a pydantic ValidationError echoing the offending model — renders
    # this as '**********' instead of the live pepper. Unwrapped ONLY inside
    # the validator below and inside plot_access_password._pepper_key_bytes().
    PLOT_ACCESS_PASSWORD_PEPPER: SecretStr = SecretStr("")
    # Deploy-time rollout switch for round 8-9C. MUST stay false until every
    # plot that needs one has a credential — flipping it early locks existing
    # phone-only users out. Round 8-9A does not read it anywhere in the public
    # flow; it exists so the pepper validator below can gate it.
    PUBLIC_PLOT_PASSWORD_ENFORCEMENT: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.API_CORS_ORIGINS.split(",") if o.strip()]

    # CIDR list parsed from TRUSTED_PROXY_IPS env. Used by app.core.rate_limit
    # to decide whether to honour X-Forwarded-For from the immediate hop.
    @property
    def trusted_proxy_networks(self) -> list[str]:
        return [n.strip() for n in self.TRUSTED_PROXY_IPS.split(",") if n.strip()]

    # Round 8-16D.2 — settings that MUST be supplied, with no default and no
    # fallback. Checked before pydantic's own required-field validation so a
    # missing one never produces a ValidationError carrying the raw input.
    # Adding a default to "fix" a failure here would be a security downgrade:
    # these are the DB credential and the token-signing key.
    _REQUIRED_SECRET_SETTINGS: ClassVar[tuple[str, ...]] = (
        "DB_PASSWORD",
        "JWT_SECRET_KEY",
    )

    # FIRST validator overall — mode="before" runs ahead of field parsing,
    # which is the whole point: pydantic must never get far enough to build a
    # ValidationError for these fields.
    #
    # Verified against pydantic-settings before relying on it (round 8-16D.2):
    #   * `data` arrives as a dict of MERGED sources — env vars, .env, and
    #     init kwargs — not just the init kwargs, so a value supplied purely
    #     through the environment is visible here.
    #   * raising from here pre-empts required-field validation entirely.
    # Both are load-bearing; re-verify them if pydantic-settings is upgraded.
    @model_validator(mode="before")
    @classmethod
    def _required_settings_present(cls, data: object) -> object:
        # Non-dict input (e.g. model_construct paths) has nothing to inspect;
        # let pydantic handle it rather than guessing.
        if not isinstance(data, dict):
            return data

        missing: list[str] = []
        for name in cls._REQUIRED_SECRET_SETTINGS:
            value = data.get(name)
            # Absent, None, or present-but-blank/whitespace all count. A
            # whitespace-only password would otherwise satisfy pydantic's
            # `str` requirement and reach the database as a real credential.
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(name)

        if missing:
            # sorted() so the message is identical run to run — the
            # _REQUIRED_SECRET_SETTINGS order must not leak into output that
            # tests and operators compare.
            raise MissingRequiredSettingsError(
                "Missing required setting(s): "
                + ", ".join(sorted(missing))
                + ". Set each one in the environment or backend/.env. "
                "Generate a signing key with:\n"
                '  python -c "import secrets; print(secrets.token_hex(32))"\n'
                "No value is shown here, and no default is applied — these "
                "are the database credential and the token-signing key."
            )
        return data

    # Round 8-16D.1 — THE SINGLE SOURCE OF TRUTH for "are we in production".
    # Every production gate (secure cookies in api/v1/auth.py, the /docs
    # switch in main.py, and the two validators below) reads this instead of
    # comparing APP_ENV inline, so they can never drift apart or disagree
    # about what counts as production.
    @property
    def is_production(self) -> bool:
        return self.APP_ENV == APP_ENV_PRODUCTION

    # FIRST after-validator on purpose: every validator below reasons about
    # APP_ENV, so an unrecognised value must be rejected before any of them
    # draw a conclusion from it. Pydantic runs mode="after" validators in
    # definition order, so this one's position is load-bearing — keep it at
    # the top.
    @model_validator(mode="after")
    def _app_env_is_supported(self) -> "Settings":
        if self.APP_ENV not in SUPPORTED_APP_ENVS:
            # APP_ENV is not a secret, but it is still echoed via a bounded
            # repr rather than interpolated raw, so a pathological value
            # cannot forge extra lines into the message.
            raise AppEnvConfigError(
                f"APP_ENV={self.APP_ENV!r} is not a supported environment. "
                f"Use one of: {', '.join(sorted(SUPPORTED_APP_ENVS))}. "
                "Values are matched exactly — 'prod' and 'Production' are "
                "NOT accepted and are never auto-corrected to 'production', "
                "because a near-miss would leave every production guard "
                "(secure cookies, /docs gating, rate-limit storage and proxy "
                "checks) silently disabled."
            )
        return self

    @model_validator(mode="after")
    def _reject_wildcard_cors_with_credentials(self) -> "Settings":
        # Block '*' in API_CORS_ORIGINS. main.py mounts CORSMiddleware
        # with allow_credentials=True; the CORS spec forbids '*' + creds
        # together (browsers drop the response, and '*' would defeat the
        # whole same-origin defence anyway). Settings() raises here so a
        # misconfigured prod fails to boot rather than silently shipping.
        if any(o.strip() == "*" for o in self.API_CORS_ORIGINS.split(",")):
            raise CorsConfigError(
                "API_CORS_ORIGINS contains '*' — wildcard origin is "
                "incompatible with allow_credentials=True. List explicit "
                "origins (comma-separated) instead."
            )
        return self

    # Round-4 HIGH-1 — reject weak / placeholder / short JWT secrets at boot.
    # Known weak strings come from common copy-paste templates; checking
    # length alone wouldn't catch "x" * 64 etc. The wizard generates a
    # 64-char hex value via secrets.token_hex(32) — no scaffolded project
    # will trip this. Manual .env weakening fails fast at app boot.
    _JWT_PLACEHOLDERS: ClassVar[set[str]] = {
        "changeme", "change-me", "secret", "supersecret", "your-secret-here",
        "test", "testing", "dev", "development", "placeholder",
    }

    @model_validator(mode="after")
    def _jwt_secret_strong_enough(self) -> "Settings":
        val = (self.JWT_SECRET_KEY or "").strip()
        if len(val) < 32:
            raise JwtSecretConfigError(
                "JWT_SECRET_KEY must be at least 32 characters. Generate one with:\n"
                "  python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if val.lower() in self._JWT_PLACEHOLDERS:
            raise JwtSecretConfigError(
                "JWT_SECRET_KEY appears to be a placeholder value — "
                "generate a real secret with secrets.token_hex(32)."
            )
        # Reject low-entropy keys (e.g. "xxxx..." or "placeholder+padding").
        # secrets.token_hex(32) produces 64 chars from a 16-char hex alphabet
        # and at that length essentially always contains all 16 distinct
        # chars (birthday paradox saturates well before 64). secrets.token_
        # urlsafe(24) produces 32 chars from a 64-char alphabet and yields
        # ~25 distinct on average. Either is far above the 12 floor here.
        # Manually-typed weak secrets like "changeme" + "x"*24 (8 distinct)
        # or "your-secret-here" + "0"*16 (11 distinct) trip this guard.
        if len(set(val)) < 12:
            raise JwtSecretConfigError(
                "JWT_SECRET_KEY entropy too low (fewer than 12 distinct chars) — "
                "generate a real secret with secrets.token_hex(32)."
            )
        return self

    # Round-4 HIGH-2 / hardened round 8-16D.1 — production must use shared
    # rate-limit storage. In-memory counters live per worker, so --workers 4
    # turns 5/min into 20/min and replicas multiply that again.
    #
    # The original check was `startswith("memory:")`, which only caught the
    # default. Blank, a malformed string, or an unsubstituted
    # .env.prod.example placeholder all sailed through the validator and then
    # blew up (or silently under-limited) far from the cause. This validates
    # the whole contract instead.
    #
    # NOTHING here connects to Redis: it is pure string analysis, so boot —
    # and every test — stays offline and fast. Reachability is a deploy-time
    # concern, not a config-parsing one.
    _SUPPORTED_RATE_LIMIT_SCHEMES: ClassVar[frozenset[str]] = frozenset({
        # Matches what backend/pyproject.toml actually installs
        # (limits[redis], the sync client) and what docs/security.md
        # documents. Choosing a different shared backend is a dependency
        # change too, so failing loudly here is correct rather than
        # permissive.
        "redis", "rediss",
    })

    @model_validator(mode="after")
    def _production_needs_shared_rate_limit_storage(self) -> "Settings":
        if not self.is_production:
            return self
        raw = self.RATE_LIMIT_STORAGE_URI.strip()

        # The URI can legitimately carry credentials (redis://user:pass@host),
        # so NO branch below ever echoes it — not even a fragment. Each
        # message names the setting and states the rule; the operator already
        # has the value in front of them.
        if not raw:
            reason = "is empty"
        elif raw.startswith("memory:"):
            reason = (
                "uses in-memory storage, which is per-worker — with "
                "`uvicorn --workers 4` every limit would be 4x looser, and "
                "each additional replica multiplies it again"
            )
        elif "<" in raw or ">" in raw:
            reason = (
                "still contains a template placeholder (did you copy "
                ".env.prod.example without substituting the real host?)"
            )
        elif "://" not in raw:
            reason = "is not a valid URI (expected '<scheme>://<host>[:port][/db]')"
        else:
            scheme = raw.split("://", 1)[0].lower()
            remainder = raw.split("://", 1)[1]
            if not remainder:
                reason = "has no host after '://'"
            elif scheme not in self._SUPPORTED_RATE_LIMIT_SCHEMES:
                reason = (
                    "does not use a supported shared backend — expected "
                    "redis:// or rediss:// (TLS), matching the limits[redis] "
                    "client this project installs"
                )
            else:
                return self

        raise RateLimitStorageConfigError(
            f"APP_ENV=production requires a shared RATE_LIMIT_STORAGE_URI, but "
            f"the configured value {reason}. Point it at the Redis instance "
            f"provided by Infra, e.g. redis://<host>:6379/0 (or rediss:// for "
            f"TLS). The value itself is not shown here because it may contain "
            f"credentials."
        )

    # Round 8-16D — production runtime preflight. Each check below closes a
    # misconfiguration that currently boots FINE and then silently degrades a
    # security control, which is strictly worse than refusing to start:
    #
    #   TRUSTED_PROXY_IPS empty — this app is always deployed behind the
    #     frontend nginx container (root docker-compose.yml gives backend
    #     `expose: 8000` with no published port, so nginx is the only path
    #     in). With no trusted proxy configured, app/core/rate_limit._client_ip
    #     falls back to request.client.host — which is ALWAYS nginx's own
    #     container IP. Every client on the internet then shares ONE rate-limit
    #     bucket: the login brute-force limit becomes 5/minute globally, and one
    #     noisy user locks everyone else out. Nothing logs an error; the limiter
    #     looks like it is working.
    #
    #   API_CORS_ORIGINS still pointing at localhost — the dev default
    #     (http://localhost:5173) shipped to production means the real SPA
    #     origin is NOT allow-listed, so authenticated cross-origin calls fail,
    #     while a developer's local machine IS allow-listed against production.
    #
    #   APP_DEBUG on — FastAPI(debug=True) serves tracebacks to the client,
    #     leaking file paths, settings frames, and query fragments.
    #
    # Deliberately NOT enforced here (needs infrastructure facts this code
    # cannot know, so a hard gate would block a legitimate deploy):
    #   DB_APP_USER — empty falls back to the owner role. 5 tables use FORCE
    #     ROW LEVEL SECURITY so they stay protected either way, but the rest
    #     rely on the non-owner srm_app role for RLS. Whether that role exists
    #     on the target database is an Infra question — tracked as a blocker
    #     in docs/deployment.md rather than guessed at here.
    @model_validator(mode="after")
    def _production_runtime_preflight(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        raw_proxies = self.trusted_proxy_networks
        # Parse, don't just count. app/core/rate_limit._parse_proxy_networks
        # DROPS entries that aren't valid CIDRs (a warning to stderr, easily
        # missed) — so an unsubstituted template value like
        # "<nginx-container-cidr>" is a non-empty string that yields ZERO
        # usable networks, silently reproducing the exact single-bucket
        # failure this check exists to prevent. Validate what will actually
        # be used, not what was merely typed.
        invalid = [p for p in raw_proxies if not _is_valid_cidr(p)]
        if not raw_proxies:
            problems.append(
                "TRUSTED_PROXY_IPS is empty — behind the nginx reverse proxy "
                "every request would be attributed to the proxy's own IP and "
                "all clients would share a single rate-limit bucket. Set it to "
                "the CIDR(s) of the reverse proxy hop(s) in front of this app."
            )
        elif invalid:
            # Names the offending entries — these are infrastructure CIDRs,
            # not secrets.
            problems.append(
                f"TRUSTED_PROXY_IPS contains {len(invalid)} entr"
                f"{'y' if len(invalid) == 1 else 'ies'} that "
                f"{'is' if len(invalid) == 1 else 'are'} not a valid IP/CIDR "
                f"and would be silently dropped: {invalid!r}. "
                "(Did you copy .env.prod.example without substituting the "
                "placeholders?)"
            )
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append(
                "API_CORS_ORIGINS still contains a localhost origin — that is "
                "the development default. List the real production origin(s)."
            )
        if self.APP_DEBUG:
            problems.append(
                "APP_DEBUG is true — debug mode returns tracebacks to clients. "
                "Set APP_DEBUG=false in production."
            )
        if problems:
            # Every problem at once, not one-at-a-time: an operator fixing a
            # production deploy should see the whole list in one boot attempt.
            raise ProductionConfigError(
                "APP_ENV=production preflight failed:\n  - " + "\n  - ".join(problems)
            )
        return self

    # Round 8-9A — the plot inspection password's blind index is only as strong
    # as its pepper, and it must be its OWN secret. Three failures are fatal at
    # boot rather than silently degrading to a guessable index:
    #   1. enforcement on with no pepper — every public lookup would 503, i.e.
    #      a full outage of the inspection flow. Fail here instead, loudly.
    #   2. pepper == JWT_SECRET_KEY — one leaked value would compromise both
    #      token signing and every stored credential digest.
    #   3. a short pepper — same >=32-char floor JWT_SECRET_KEY already uses.
    # A blank pepper with enforcement OFF is fine (the round 8-9A state): only
    # admin credential mutations are unavailable, and they answer 503.
    # Every message below names the SETTING, never its value — a
    # ValidationError is rendered into logs and CI output verbatim.
    @model_validator(mode="after")
    def _plot_access_pepper_is_usable(self) -> "Settings":
        # Unwrapped here and nowhere else in this module; the local goes out of
        # scope with the validator.
        pepper = (self.PLOT_ACCESS_PASSWORD_PEPPER.get_secret_value() or "").strip()
        if self.PUBLIC_PLOT_PASSWORD_ENFORCEMENT and not pepper:
            raise PlotAccessPepperConfigError(
                "PUBLIC_PLOT_PASSWORD_ENFORCEMENT=true requires "
                "PLOT_ACCESS_PASSWORD_PEPPER to be set. Generate one with:\n"
                "  python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if pepper:
            if pepper == (self.JWT_SECRET_KEY or "").strip():
                raise PlotAccessPepperConfigError(
                    "PLOT_ACCESS_PASSWORD_PEPPER must not reuse JWT_SECRET_KEY — "
                    "generate a separate secret with secrets.token_hex(32)."
                )
            if len(pepper) < 32:
                raise PlotAccessPepperConfigError(
                    "PLOT_ACCESS_PASSWORD_PEPPER must be at least 32 characters. "
                    "Generate one with:\n"
                    "  python -c \"import secrets; print(secrets.token_hex(32))\""
                )
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def database_runtime_url(self) -> str:
        """Async engine URL for the app runtime pool.

        Uses DB_APP_USER (no BYPASSRLS) when configured so Postgres RLS
        policies are enforced.  Falls back to DB_USER (owner) when
        DB_APP_USER is empty — safe for local dev before Step 8 migration.
        """
        if self.DB_APP_USER and self.DB_APP_PASSWORD:
            return (
                f"postgresql+asyncpg://{self.DB_APP_USER}:{self.DB_APP_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Module-level singleton — feature modules (e.g., modules/auth) do
# `from app.core.config import settings`. get_settings() is the factory
# preferred by host code that wants the @lru_cache scoping; both reach
# the same instance.
settings = get_settings()
