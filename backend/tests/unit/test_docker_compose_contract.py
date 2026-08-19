"""Docker Compose / nginx contract guards (round 8-16C).

Regression guards for the contracts established in rounds 8-16A/8-16B:
production media persistence, the dev-vs-prodlike media mount split,
prodlike's no-source-bind/no-reload guarantee, the frontend build-arg
contract, the DB no-touch (`--no-deps`) canonical command, and nginx's
`/media` deny + same-origin `/api` proxy + upload-limit + `/assets` header
contracts.

Structured parsing only — no fragile regex over raw YAML:
`docker compose ... config --format json` does the REAL multi-file merge
(env interpolation, override-by-target for volumes, etc.) and we assert on
the resulting JSON, exactly the way Compose itself resolves it. This catches
merge-order bugs a regex over the source YAML never would. The nginx checks
use a brace-depth block extractor (handles nesting correctly) rather than
line-count-based regex; the backend upload contract is read via a real
`import`, not by parsing the module source.

Every Docker-dependent test module-skips (not fails) when the `docker` CLI
or `backend/.env` isn't available — this suite is a real regression guard on
a dev machine with the local stack set up (which is exactly this project's
documented baseline, see docs/deployment.md), not a hard CI dependency.
`docker compose config` never touches the daemon or any running container —
these tests are read-only regardless of what's currently up.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_ROOT = REPO_ROOT / "docker-compose.yml"
COMPOSE_DEV = REPO_ROOT / "docker-compose.dev.yml"
COMPOSE_PRODLIKE = REPO_ROOT / "docker-compose.prodlike.yml"
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"
ENV_FILE = REPO_ROOT / "backend" / ".env"
MEDIA_TARGET = "/app/var/inspection-photos"

_HAS_DOCKER = shutil.which("docker") is not None
_HAS_ENV_FILE = ENV_FILE.exists()
requires_docker = pytest.mark.skipif(not _HAS_DOCKER, reason="docker CLI not found")
requires_env_file = pytest.mark.skipif(
    not _HAS_ENV_FILE, reason="backend/.env not present (needed by dev/prodlike overlays)"
)


def _compose_config(*files: Path, with_env: bool = False) -> dict:
    """Real merged config via the Compose CLI — never touches the daemon."""
    cmd = ["docker", "compose"]
    if with_env:
        cmd += ["--env-file", str(ENV_FILE), "-p", "srm_fieldinspect"]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["config", "--format", "json"]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    return json.loads(result.stdout)


def _volume_at_target(volumes: list[dict], target: str) -> list[dict]:
    return [v for v in volumes if v.get("target") == target]


# --- 1/2/3: media mount contract across root / dev / prodlike -------------

@requires_docker
def test_root_backend_mounts_external_media_volume_at_correct_target():
    cfg = _compose_config(COMPOSE_ROOT)
    vols = cfg["services"]["backend"].get("volumes", [])
    hits = _volume_at_target(vols, MEDIA_TARGET)
    assert len(hits) == 1, f"expected exactly one mount at {MEDIA_TARGET}, got {hits}"
    assert hits[0]["type"] == "volume"
    assert hits[0]["source"] == "media"

    media = cfg["volumes"]["media"]
    assert media["external"] is True
    assert media["name"] == "srm-fieldinspect-media"


@requires_docker
@requires_env_file
def test_dev_overlay_replaces_media_target_with_host_bind():
    cfg = _compose_config(COMPOSE_ROOT, COMPOSE_DEV, with_env=True)
    vols = cfg["services"]["backend"].get("volumes", [])
    hits = _volume_at_target(vols, MEDIA_TARGET)
    # Exactly one — Compose merges service volumes BY TARGET, so dev's bind
    # declaration must REPLACE the root volume declaration, never coexist
    # alongside it (a duplicate target would mean the merge silently kept
    # both and Docker picked one arbitrarily).
    assert len(hits) == 1, f"expected exactly one mount at {MEDIA_TARGET}, got {hits}"
    assert hits[0]["type"] == "bind", (
        "dev overlay must override the media target with a host bind mount "
        "so local dev keeps writing photos to backend/var/inspection-photos"
    )
    source = hits[0]["source"].replace("\\", "/")
    assert source.endswith("backend/var/inspection-photos")


@requires_docker
@requires_env_file
def test_prodlike_backend_has_no_source_bind_mount_and_no_reload():
    cfg = _compose_config(COMPOSE_ROOT, COMPOSE_PRODLIKE, with_env=True)
    backend = cfg["services"]["backend"]
    vols = backend.get("volumes", [])

    # No whole-source bind at /app (that's what dev's hot-reload mount uses).
    assert not _volume_at_target(vols, "/app"), (
        "prodlike backend must not bind-mount the source tree at /app"
    )
    # The media mount itself must still be the named volume, not a bind.
    hits = _volume_at_target(vols, MEDIA_TARGET)
    assert len(hits) == 1
    assert hits[0]["type"] == "volume"

    command = backend.get("command")
    if command is not None:
        assert "--reload" not in command, "prodlike backend must not run with --reload"


# --- 4/5: frontend build-arg contract ---------------------------------------

_REQUIRED_VITE_ARGS = {
    "VITE_APP_NAME", "VITE_API_BASE_URL", "VITE_PUBLIC_APP_URL",
    "VITE_AUTH_SCOPE", "VITE_AZURE_AD_TENANT_ID", "VITE_AZURE_AD_CLIENT_ID",
    "VITE_AZURE_AD_REDIRECT_URI", "VITE_DEFAULT_LANGUAGE",
}


@requires_docker
def test_root_frontend_has_all_eight_vite_build_args():
    cfg = _compose_config(COMPOSE_ROOT)
    args = cfg["services"]["frontend"]["build"].get("args") or {}
    missing = _REQUIRED_VITE_ARGS - set(args)
    assert not missing, f"root frontend build.args is missing: {sorted(missing)}"


@requires_docker
@requires_env_file
def test_prodlike_local_redirect_targets_localhost_8080():
    cfg = _compose_config(COMPOSE_ROOT, COMPOSE_PRODLIKE, with_env=True)
    args = cfg["services"]["frontend"]["build"].get("args") or {}
    assert args.get("VITE_AZURE_AD_REDIRECT_URI") == "http://localhost:8080/auth/callback"


# --- 6: production has no db service ----------------------------------------

@requires_docker
def test_root_production_has_no_db_service():
    cfg = _compose_config(COMPOSE_ROOT)
    assert "db" not in cfg["services"], (
        "root docker-compose.yml is production — DB lives on a centralized "
        "server (see file header) and must never gain a db service"
    )


# --- 7: --no-deps present on every documented prodlike `up` command --------

def _joined_logical_lines(text: str) -> list[str]:
    """Join lines connected by a trailing backslash continuation into one
    logical line — handles both the compose file's YAML comment block and
    docs/deployment.md's ```bash fences without depending on either format's
    exact indentation."""
    lines = text.splitlines()
    joined: list[str] = []
    buf = ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
        else:
            buf += stripped
            joined.append(buf)
            buf = ""
    if buf:
        joined.append(buf)
    return joined


@pytest.mark.parametrize("path", [COMPOSE_PRODLIKE, DEPLOYMENT_DOC])
def test_prodlike_canonical_up_commands_include_no_deps(path: Path):
    text = path.read_text(encoding="utf-8")
    offenders = [
        line for line in _joined_logical_lines(text)
        if "docker-compose.prodlike.yml" in line
        and re.search(r"\bup\b", line)
        and "--no-deps" not in line
    ]
    assert not offenders, (
        f"{path.name} has a prodlike `up` command missing --no-deps "
        f"(backend depends_on db, so without it Compose traverses to the "
        f"db service on every prodlike up/rebuild): {offenders}"
    )


# --- db service contract: dev and prodlike must not drift -------------------

@requires_docker
@requires_env_file
def test_db_service_definition_identical_between_dev_and_prodlike():
    """`docker-compose.dev.yml` and `docker-compose.prodlike.yml` each declare
    their own `db` service, and the prodlike canonical command's safety rests
    on those two declarations being equivalent: prodlike passes --no-deps so
    it never traverses to db, but anyone who forgets the flag would have
    Compose compare the merged db config against the RUNNING container and
    RECREATE it if they differ.

    Comparing the fully-merged, Compose-resolved service dicts (not the raw
    YAML text) means formatting, comments and key order can differ freely
    while any behavioural drift — image, volumes, ports, healthcheck, env —
    fails this test.
    """
    dev_db = _compose_config(COMPOSE_ROOT, COMPOSE_DEV, with_env=True)["services"]["db"]
    prodlike_db = _compose_config(
        COMPOSE_ROOT, COMPOSE_PRODLIKE, with_env=True,
    )["services"]["db"]

    differing = sorted(
        key for key in set(dev_db) | set(prodlike_db)
        if dev_db.get(key) != prodlike_db.get(key)
    )
    assert not differing, (
        "db service drifted between dev and prodlike on: "
        f"{differing}. Keep the two declarations equivalent, or a prodlike "
        "`up` without --no-deps will recreate the database container."
    )


# --- nginx: brace-depth block extractor (not line-count regex) -------------

def _extract_block(text: str, header_pattern: str) -> str:
    m = re.search(header_pattern, text)
    assert m, f"block header not found in nginx.conf: {header_pattern!r}"
    brace_start = text.index("{", m.end())
    depth = 1
    i = brace_start + 1
    while depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[brace_start + 1 : i - 1]


@pytest.fixture(scope="module")
def nginx_text() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


# --- 8: /media denied ---------------------------------------------------

def test_nginx_media_prefix_returns_404(nginx_text: str):
    block = _extract_block(nginx_text, r"location\s+\^~\s+/media/\s*")
    assert "return 404" in block


def test_nginx_media_exact_returns_404(nginx_text: str):
    block = _extract_block(nginx_text, r"location\s+=\s+/media\s*")
    assert "return 404" in block


# --- 9: /api proxy preserves the prefix -------------------------------------

def test_nginx_api_proxy_preserves_prefix(nginx_text: str):
    block = _extract_block(nginx_text, r"location\s+/api/\s*")
    # proxy_pass with NO URI part after the authority is what makes nginx
    # forward the ORIGINAL request URI (including /api) untouched — a
    # trailing path/slash here would instead strip the /api prefix.
    assert re.search(r"proxy_pass\s+http://srm-fieldinspect-backend:8000\s*;", block), (
        "proxy_pass must have no URI component so /api is preserved"
    )


def test_nginx_api_proxy_targets_the_container_name_not_the_service_alias(
    nginx_text: str,
):
    """Round 8-25C regression guard — `proxy_pass http://backend:8000` sent
    ~half of every API call, Authorization header included, to a DIFFERENT
    project's backend.

    `proxy-net` is an external network shared by every app on the UAT host,
    and Docker registers each container's compose SERVICE name as an alias on
    it. A second project whose service was also named `backend` (uat-fdp)
    therefore made the bare name resolve to two containers, and Docker DNS
    round-robined between them — /api/v1/plots alternated 200 and a FastAPI
    404 from the other app's router. The container name is unique host-wide,
    so it must stay the target here.
    """
    block = _extract_block(nginx_text, r"location\s+/api/\s*")
    # Comments stripped first: the directive's own comment quotes the broken
    # `proxy_pass http://backend:8000` as the thing NOT to go back to, and
    # matching that text would fail the test on the very explanation of it.
    directives = "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"proxy_pass\s+https?://backend[:/]", directives), (
        "proxy_pass must not use the ambiguous compose service name `backend` "
        "— it collides with other projects on the shared proxy-net network"
    )


# --- 10: upload limit covers the backend's real contract --------------------

def _nginx_size_to_bytes(value: str) -> int:
    m = re.match(r"^(\d+)([kKmMgG]?)$", value.strip())
    assert m, f"unrecognised nginx size value: {value!r}"
    n, unit = int(m.group(1)), m.group(2).lower()
    mult = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit]
    return n * mult


def test_nginx_upload_limit_covers_backend_photo_contract(nginx_text: str):
    from app.services.inspection_photos import MAX_PHOTO_COUNT, MAX_PHOTO_UPLOAD_BYTES

    m = re.search(r"client_max_body_size\s+(\S+)\s*;", nginx_text)
    assert m, "client_max_body_size not set in nginx.conf"
    nginx_bytes = _nginx_size_to_bytes(m.group(1))
    backend_ceiling = MAX_PHOTO_COUNT * MAX_PHOTO_UPLOAD_BYTES
    assert nginx_bytes >= backend_ceiling, (
        f"nginx client_max_body_size ({nginx_bytes} bytes) is BELOW the "
        f"backend's own upload ceiling ({backend_ceiling} bytes = "
        f"{MAX_PHOTO_COUNT} x {MAX_PHOTO_UPLOAD_BYTES}) — nginx would reject "
        f"a full-size upload with an opaque 413 before the backend's own "
        f"structured error ever gets a chance to fire"
    )


# --- 11: /assets/ has the full header set, Cache-Control exactly once -------

def test_nginx_assets_has_full_security_headers_and_single_cache_control(nginx_text: str):
    block = _extract_block(nginx_text, r"location\s+/assets/\s*")
    cache_control_hits = len(re.findall(r"add_header\s+Cache-Control\b", block, re.IGNORECASE))
    assert cache_control_hits == 1, (
        f"/assets/ must set Cache-Control exactly once, found {cache_control_hits}"
    )
    # `expires` ALSO emits its own Cache-Control header — pairing it with
    # add_header Cache-Control (the exact round 8-16A→8-16B regression) sends
    # the header twice. Counting add_header hits alone can't see this, since
    # `expires` isn't spelled add_header at all — checked separately.
    assert not re.search(r"(?<!\w)expires\s", block, re.IGNORECASE), (
        "/assets/ must not use `expires` alongside add_header Cache-Control "
        "— it emits a second, conflicting Cache-Control header"
    )
    for header in (
        "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
        "Strict-Transport-Security", "Content-Security-Policy",
    ):
        assert re.search(rf"add_header\s+{re.escape(header)}\b", block), (
            f"/assets/ is missing add_header {header} — nginx only inherits "
            f"add_header from the server block when the location declares "
            f"NONE of its own, so this location must repeat every header "
            f"the server block sets, not just some of them"
        )
