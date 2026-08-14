"""Inspection-record photo storage (round 13; download + cleanup round 13.1;
secure resize/compression round 8-14A; switched to WebP output round 8-14A.1).

Local/dev filesystem storage only — object storage (S3/MinIO/Azure Blob) is
explicitly out of scope. `PhotoStorage` is the seam: swapping in a real
backend later means writing a new class with `save()`/`delete()`, no changes
to validation logic or callers (app/api/v1/records.py,
app/api/v1/public_records.py).

Round 8-14A — every NEWLY uploaded photo is decoded, EXIF-rotated, colour-
normalized, stripped of all metadata, downscaled and re-encoded before it is
stored (`normalize_inspection_photo`). Round 8-14A.1 switched the OUTPUT
format from JPEG to WebP (still Pillow, no new dependency — the same build
already had `webp` codec support, verified via `PIL.features.check("webp")`
before this round touched anything). Two separate size contracts apply and
must not be conflated:

  * MAX_PHOTO_UPLOAD_BYTES  — what a client may SEND (15 MiB/photo).
  * MAX_STORED_PHOTO_BYTES  — what may ever land on disk (1.5 MiB/photo, hard;
    tightened from 8-14A's 2 MiB now that WebP's better compression makes a
    smaller ceiling realistic).

Photos stored BEFORE round 8-14A.1 are left exactly as they are: no batch
resize, no rename, no re-encode — this applies to 8-14A's own JPEG writes
just as much as the pre-8-14A png/webp ones. That is why
PHOTO_FILENAME_PATTERN and `_MEDIA_TYPES` still accept `jpg`/`png`/`webp`
even though new writes are now always `.webp` — the other two extensions are
read-only history now.

Only URLs/paths are ever persisted to records.photo_urls — never binary
content in the DB (per the round 13 brief).

`INSPECTION_PHOTOS_URL_PREFIX` is not served by a static-file mount — these
are supplier field photos gated by the same RLS/permission scope as the
record itself, so a plain `StaticFiles` mount would let anyone with a URL
bypass that scope entirely (docs/security.md §3.5 also forbids serving
uploads from a web-server-static directory). Round 13.1 instead adds a
scoped download route (`GET /api/v1/records/{record_id}/photos/{photo_id}`
in app/api/v1/records.py) that re-checks RLS/permission scope on every
request via the normal record-read path, then serves the file directly —
`resolve_existing_path`/`delete` below exist to support that route and the
orphan-cleanup guard, and are deliberately on `LocalPhotoStorage` rather
than the generic `PhotoStorage` protocol, since "resolve a local path" and
"delete by filename" aren't meaningful for a future non-local backend (that
would instead mint a presigned URL / call a delete API).

No public (unauthenticated) photo download exists — see app/api/v1/
public_records.py's module docstring for why that's deferred, not silently
dropped.
"""
from __future__ import annotations

import asyncio
import io
import warnings
import weakref
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import structlog
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageCms, ImageFile, ImageOps, UnidentifiedImageError

logger = structlog.get_logger()

# Photos are optional per the field-UX change that also added the 5th
# "ปัญหาอื่นๆ" slot: a caller with zero photos uses the plain JSON create
# endpoint instead (this multipart path always carries at least one file
# part), so the validation below caps the count rather than requiring an
# exact number.
MAX_PHOTO_COUNT = 5

# --- Round 8-14A size contracts (ceiling tightened round 8-14A.1) ---------
# INPUT cap: the largest single file a client may upload. Deliberately much
# larger than the stored cap because a modern phone camera JPEG routinely
# exceeds 5 MiB and the server now shrinks it for the user.
MAX_PHOTO_UPLOAD_BYTES = 15 * 1024 * 1024
# OUTPUT contracts: what may actually be written to storage. TARGET is what
# the encoder aims for; MAX is an inviolable ceiling — a photo that cannot be
# brought under it within the safety floor below is rejected, never stored.
# Round 8-14A.1: lowered from 1.8/2.0 MiB (JPEG) to 1.2/1.5 MiB now that WebP
# reaches the same visual quality at a smaller byte budget.
TARGET_STORED_PHOTO_BYTES = int(1.2 * 1024 * 1024)
MAX_STORED_PHOTO_BYTES = int(1.5 * 1024 * 1024)

# Decode guards. MAX_IMAGE_PIXELS is checked from the HEADER, before any full
# decode, so a "pixel bomb" (a tiny file declaring a huge canvas) is refused
# before it can allocate that canvas.
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_EDGE = 2_560
# Safety floor for the downscale ladder: we never shrink below this just to
# hit the byte budget. If even this can't fit under MAX_STORED_PHOTO_BYTES,
# the upload is rejected rather than degraded into uselessness.
MIN_IMAGE_EDGE = 1_280

# Round 8-14A.1 — WebP quality ladder (was JPEG's INITIAL_JPEG_QUALITY /
# MIN_JPEG_QUALITY / _JPEG_QUALITY_STEPS through round 8-14A).
INITIAL_WEBP_QUALITY = 85
MIN_WEBP_QUALITY = 75
# Descending ladder tried at each candidate size; the first result at or under
# TARGET wins. Ends exactly on MIN_WEBP_QUALITY — never below.
_WEBP_QUALITY_STEPS: tuple[int, ...] = (85, 82, 80, 78, 76, 75)
# Total encode budget across ALL size/quality attempts for one photo. Purely a
# runaway guard: the ladders above are finite on their own.
_MAX_ENCODE_ATTEMPTS = 40
# Each downscale step multiplies the longest edge by this, floored at
# MIN_IMAGE_EDGE.
_DOWNSCALE_STEP = 0.85
# Pillow's WebP encoder effort/speed knob (0-6). 4 is the library's own
# default and a deliberate middle ground: 6 buys a few more % of compression
# for meaningfully more CPU, and every upload already competes for one of
# only MAX_CONCURRENT_IMAGE_JOBS worker slots across up to MAX_PHOTO_COUNT
# photos per request — raising it needs a benchmark first, not a guess.
_WEBP_ENCODE_METHOD = 4

# Every new photo is stored as WebP regardless of what was uploaded (round
# 8-14A.1; was JPEG through round 8-14A).
STORED_PHOTO_EXTENSION = "webp"

# How many photos may be decoded/encoded at once in this process. Pillow work
# is CPU- and RAM-heavy and runs off the event loop in worker threads; without
# a bound, five concurrent 5-photo uploads would try to hold 25 decoded
# bitmaps at once.
MAX_CONCURRENT_IMAGE_JOBS = 2

# Matches exactly what LocalPhotoStorage.save() generates (uuid4().hex + one
# of the allowlisted extensions) — used both as a FastAPI Path(pattern=...)
# gate on the download route (rejects path-traversal-shaped input with a
# 422 before any lookup happens) and to validate filenames pulled out of
# stored photo_urls.
#
# Round 8-14A.1: new saves are always `.webp` now, but `jpg` (8-14A's own
# output) AND `png` (pre-8-14A) MUST both stay in this pattern — photos
# stored by earlier rounds keep their original extension forever and their
# download route would 422 on the filename gate if either were dropped.
PHOTO_FILENAME_PATTERN = r"^[0-9a-f]{32}\.(?:jpg|png|webp)$"

_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES[filename.rsplit(".", 1)[-1]]


def photo_filename_from_url(url: str) -> str:
    """records.photo_urls stores `{url_prefix}/{filename}` — this pulls the
    filename back out. Safe to do unconditionally: the result is only ever
    used as an equality check against a client-supplied photo_id (never
    passed straight to the filesystem), so there's nothing here for a
    malformed stored URL to exploit."""
    return url.rsplit("/", 1)[-1]

# Checked against actual bytes, never the client-supplied Content-Type
# header or filename — both are attacker-controlled and would let a
# renamed/relabelled non-image file (e.g. a script) through the allowlist.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


def _sniff_extension(content: bytes) -> str | None:
    for signature, ext in _MAGIC_SIGNATURES:
        if content.startswith(signature):
            return ext
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


async def _read_capped(file: UploadFile) -> bytes:
    """Read in chunks, rejecting once the size cap is crossed, so a single
    oversized upload can't buffer unbounded memory before we notice.

    The 413 fires on the chunk that CROSSES the cap and stops reading there —
    it never drains the rest of an oversized body first.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_PHOTO_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Photo '{file.filename}' exceeds the "
                    f"{MAX_PHOTO_UPLOAD_BYTES // (1024 * 1024)}MB upload limit"
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


# --- Round 8-14A: secure decode / normalize / re-encode --------------------


class PhotoProcessingError(Exception):
    """Raised by `normalize_inspection_photo` for any input it refuses.

    Carries ONLY a caller-safe reason string. Raw Pillow/parser exceptions are
    caught at the boundary and never propagate to a response: their messages
    can leak decoder internals, offsets, and (for some formats) fragments of
    file content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _open_image_strict(content: bytes) -> Image.Image:
    """Open from a header read only — no full decode yet.

    Truncated input must NOT be tolerated: `LOAD_TRUNCATED_IMAGES` is forced
    False here rather than trusting the import-time default, because it is a
    Pillow-global that any other library in the process could flip on.
    Setting it per call is safe under our bounded worker pool (every writer
    writes the same value).
    """
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        return Image.open(io.BytesIO(content))
    except UnidentifiedImageError as exc:
        raise PhotoProcessingError("unreadable or unsupported image") from exc
    except Image.DecompressionBombError as exc:
        raise PhotoProcessingError("image dimensions are too large") from exc
    except (OSError, ValueError) as exc:
        raise PhotoProcessingError("malformed image") from exc


def _reject_oversized_canvas(image: Image.Image) -> None:
    """Header-only pixel-bomb guard, run BEFORE `load()`.

    Pillow's own bomb check only *warns* between MAX_IMAGE_PIXELS and 2x that
    threshold, so this explicit comparison — not the warning — is what makes
    the limit real.
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        raise PhotoProcessingError("malformed image")
    if width * height > MAX_IMAGE_PIXELS:
        raise PhotoProcessingError("image dimensions are too large")


def _reject_animated(image: Image.Image) -> None:
    """Multi-frame input (animated WebP/PNG) is refused: only the first frame
    would survive the re-encode, silently discarding what the user sent.
    """
    if getattr(image, "n_frames", 1) > 1 or getattr(image, "is_animated", False):
        raise PhotoProcessingError("animated images are not supported")


def _flatten_onto_white(image: Image.Image) -> Image.Image:
    """Composite any transparency onto white and return an RGB image.

    The output format is WebP, which CAN carry an alpha channel — but this
    pipeline flattens onto white regardless, so every stored inspection photo
    behaves identically (no viewer-dependent transparency rendering) and
    matches the field-photo use case, where a transparent pixel almost always
    means "PNG screenshot/graphic", not an intentional see-through photo.
    """
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image if image.mode == "RGB" else image.convert("RGB")
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def _to_srgb(image: Image.Image, icc_profile: bytes | None) -> Image.Image:
    """Convert an RGB image from its embedded profile to sRGB.

    A malformed/unsupported profile is NOT an error for the upload: we fall
    back to treating the pixels as already-sRGB, which is what every viewer
    does with an unprofiled image anyway. Only the colours are approximate —
    nothing is leaked and nothing crashes.
    """
    if not icc_profile:
        return image
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        target = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(
            image, source, target, outputMode="RGB", renderingIntent=0,
        ) or image
    except Exception:  # noqa: BLE001 — any CMS failure degrades to "assume sRGB"
        logger.info("inspection_photo_icc_fallback")
        return image


def _resized_to_edge(image: Image.Image, longest_edge: int) -> Image.Image:
    """Scale so the longest side is `longest_edge`, preserving aspect ratio.

    Never upscales — an image already within the budget is returned as-is, so
    a 640x480 field photo stays 640x480 instead of being blown up and
    re-compressed into something larger and blurrier than the original.
    """
    width, height = image.size
    current = max(width, height)
    if current <= longest_edge:
        return image
    scale = longest_edge / current
    return image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.LANCZOS,
    )


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    """Encode to WebP carrying NO metadata.

    `image.info` is cleared rather than merely "not passed through": several
    Pillow operations copy `info` onto their result, so an EXIF/ICC/XMP blob
    can survive transforms and then be re-emitted by the encoder. Clearing it
    on the object we are about to save is what guarantees the output has no
    EXIF, no GPS, no ICC profile, and no XMP — Pillow's WebP writer will
    happily embed any of the four if `info` (or an explicit `exif=`/
    `icc_profile=`/`xmp=` kwarg) carries them, so omitting the kwargs AND
    clearing `info` are both required, not redundant.

    `lossless=False` is explicit (not just "the default") because a lossless
    encode ignores `quality` as a compression-ratio dial entirely — it would
    silently defeat the whole size-budget ladder in `_compress_within_budget`.
    """
    image.info = {}
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="WEBP",
        lossless=False,
        quality=quality,
        method=_WEBP_ENCODE_METHOD,
    )
    return buffer.getvalue()


def _compress_within_budget(image: Image.Image) -> bytes:
    """Find the best WebP under the size contract.

    Strategy, in priority order:
      1. At the current size, walk quality 85 -> 75. The first result at or
         under TARGET wins immediately.
      2. If quality 75 still exceeds the HARD max, and only then, shrink the
         longest edge one step and repeat. Dimensions are never reduced merely
         to reach TARGET — only to satisfy the inviolable ceiling.
      3. Stop at MIN_IMAGE_EDGE (or at the image's own size, whichever is
         larger). Still too big -> refuse; we do not store a photo over the
         ceiling and we do not shrink it into uselessness.
    """
    longest_edge = min(max(image.size), MAX_IMAGE_EDGE)
    attempts = 0
    while True:
        candidate = _resized_to_edge(image, longest_edge)
        encoded = b""
        for quality in _WEBP_QUALITY_STEPS:
            if attempts >= _MAX_ENCODE_ATTEMPTS:
                raise PhotoProcessingError("image could not be compressed within the size limit")
            attempts += 1
            encoded = _encode_webp(candidate, quality)
            if len(encoded) <= TARGET_STORED_PHOTO_BYTES:
                return encoded
        # Quality ladder exhausted at MIN_WEBP_QUALITY. Over target but still
        # within the hard ceiling is an acceptable outcome.
        if len(encoded) <= MAX_STORED_PHOTO_BYTES:
            return encoded
        next_edge = max(MIN_IMAGE_EDGE, int(longest_edge * _DOWNSCALE_STEP))
        if next_edge >= longest_edge:
            # Already at the safety floor (or the source is smaller than it)
            # and still over the ceiling — refuse rather than store or degrade.
            raise PhotoProcessingError("image could not be compressed within the size limit")
        longest_edge = next_edge


def normalize_inspection_photo(content: bytes) -> bytes:
    """Decode, sanitize, downscale and re-encode one photo as WebP (round
    8-14A.1 — was JPEG through round 8-14A).

    Pure and synchronous so it can be unit-tested directly on bytes; callers
    on the request path must use `normalize_inspection_photo_async`, which
    keeps this CPU-bound work off the event loop.

    Guarantees on every returned value:
      * starts with the WebP container magic bytes (`RIFF....WEBP`),
      * is at most MAX_STORED_PHOTO_BYTES,
      * carries no EXIF, no GPS, no ICC profile, and no XMP,
      * has its longest edge at most MAX_IMAGE_EDGE,
      * preserves the source aspect ratio (no crop, no stretch).

    Raises PhotoProcessingError — never a raw decoder exception — for input it
    refuses.
    """
    with warnings.catch_warnings():
        # Pillow signals a suspected bomb between MAX_IMAGE_PIXELS and 2x it
        # as a warning; promote it so it can never be silently ignored.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with _open_image_strict(content) as opened:
                _reject_oversized_canvas(opened)
                _reject_animated(opened)
                icc_profile = opened.info.get("icc_profile")
                try:
                    oriented = ImageOps.exif_transpose(opened) or opened
                    flattened = _flatten_onto_white(oriented)
                except PhotoProcessingError:
                    raise
                except (OSError, ValueError, SyntaxError) as exc:
                    # Raised here (not at open) for truncated/corrupt payloads,
                    # because this is the first step that forces a full decode.
                    raise PhotoProcessingError("malformed or truncated image") from exc
                srgb = _to_srgb(flattened, icc_profile)
                return _compress_within_budget(srgb)
        except PhotoProcessingError:
            raise
        except Image.DecompressionBombWarning as exc:
            raise PhotoProcessingError("image dimensions are too large") from exc
        except Image.DecompressionBombError as exc:
            raise PhotoProcessingError("image dimensions are too large") from exc
        except MemoryError as exc:
            raise PhotoProcessingError("image is too large to process") from exc
        except Exception as exc:  # noqa: BLE001 — decoder internals never reach a response
            raise PhotoProcessingError("malformed image") from exc


# One semaphore per running event loop. asyncio primitives bind to the loop
# that first awaits them and raise if reused from another, so a single
# module-level instance would break the moment a second loop existed (every
# async test gets a fresh one). A process serves exactly one loop, so this is
# still "at most MAX_CONCURRENT_IMAGE_JOBS jobs per process" in production.
_processing_semaphores: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _processing_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _processing_semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_JOBS)
        _processing_semaphores[loop] = semaphore
    return semaphore


async def normalize_inspection_photo_async(content: bytes) -> bytes:
    """Request-path wrapper: bounded concurrency, off the event loop.

    Decoding a 50-megapixel image takes hundreds of milliseconds of pure CPU;
    running it inline would stall every other request on the worker. The
    semaphore additionally caps how many such jobs may be in flight, so a
    burst of concurrent 5-photo uploads can't exhaust CPU/RAM.
    """
    async with _processing_semaphore():
        return await asyncio.to_thread(normalize_inspection_photo, content)


class PhotoStorage(Protocol):
    async def save(self, content: bytes, extension: str) -> str:
        """Persist `content` and return its retrievable URL/path."""
        ...

    async def delete(self, filename: str) -> None:
        """Remove a previously saved object by its generated filename/key.

        Round 8-14A added this to the protocol (it was already on
        LocalPhotoStorage) because `validate_and_save_photos` must be able to
        roll back a partially-written batch: saving photo 3 of 5 and failing
        would otherwise strand photos 1 and 2 on disk forever, referenced by
        no record. Deliberately keyed by the opaque filename `save()` minted —
        NOT a filesystem path — so an object-storage implementation can honour
        it with a plain delete-by-key.

        Implementations must be idempotent: deleting something already gone is
        a no-op, not an error.
        """
        ...


class LocalPhotoStorage:
    """Dev/local filesystem storage. Filenames are always server-generated
    (uuid4) — the client-supplied filename is never used to build a path,
    so there is nothing for a `../../x`-style traversal attempt to act on."""

    def __init__(self, root: Path, url_prefix: str) -> None:
        self._root = root
        self._url_prefix = url_prefix.rstrip("/")

    async def save(self, content: bytes, extension: str) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}.{extension}"
        await asyncio.to_thread((self._root / filename).write_bytes, content)
        return f"{self._url_prefix}/{filename}"

    def resolve_existing_path(self, filename: str) -> Path:
        """Round 13.1 download support. Raises FileNotFoundError uniformly
        for "invalid shape", "escapes root", and "doesn't exist" — callers
        (the download route) map all three to the same generic 404, so
        there's no distinguishable signal for an attacker to probe with.

        The route already gates `photo_id` with PHOTO_FILENAME_PATTERN
        before this is ever called, so `/`, `\\`, and `..` can't actually
        reach here in practice — the checks below are defense-in-depth in
        case this method is ever called from somewhere that skips that gate.
        """
        if "/" in filename or "\\" in filename or ".." in filename:
            raise FileNotFoundError(filename)
        candidate = (self._root / filename).resolve()
        if candidate.parent != self._root.resolve():
            raise FileNotFoundError(filename)
        if not candidate.is_file():
            raise FileNotFoundError(filename)
        return candidate

    async def delete(self, filename: str) -> None:
        """Best-effort delete, used only for orphan-cleanup after a failed
        create. Idempotent: an invalid or already-gone filename is a silent
        no-op (the caller doesn't need "there was nothing to delete" to be
        an error) — genuine OS errors (permission, file-in-use) still
        propagate so the caller can log them."""
        if "/" in filename or "\\" in filename or ".." in filename:
            return
        candidate = (self._root / filename).resolve()
        if candidate.parent != self._root.resolve():
            return
        try:
            await asyncio.to_thread(candidate.unlink)
        except FileNotFoundError:
            pass


def get_photo_storage() -> LocalPhotoStorage:
    from app.core.config import get_settings

    settings = get_settings()
    return LocalPhotoStorage(
        root=Path(settings.INSPECTION_PHOTOS_DIR),
        url_prefix=settings.INSPECTION_PHOTOS_URL_PREFIX,
    )


async def validate_and_save_photos(
    files: list[UploadFile], storage: PhotoStorage
) -> list[str]:
    """Enforce 1..MAX_PHOTO_COUNT allowlisted images, normalize each one, and
    persist them, returning URLs in the same order as `files`.

    Round 8-14A: every photo is fully validated AND normalized (decoded,
    EXIF-rotated, metadata-stripped, downscaled, re-encoded — as WebP since
    round 8-14A.1 — see `normalize_inspection_photo`) before ANY of them is
    saved. A batch whose
    fourth photo is a corrupt file therefore writes nothing at all, rather
    than leaving three orphans behind.

    Should a save nonetheless fail part-way through (disk full, permissions),
    the photos already written in THIS call are deleted before the original
    error is re-raised — the caller's own except-block cleanup only knows
    about URLs it received, which on a raised call is nothing.
    """
    if not 1 <= len(files) <= MAX_PHOTO_COUNT:
        raise HTTPException(
            status_code=422,
            detail=f"Between 1 and {MAX_PHOTO_COUNT} photos are allowed (got {len(files)})",
        )

    normalized: list[bytes] = []
    for file in files:
        content = await _read_capped(file)
        if not content:
            raise HTTPException(status_code=422, detail=f"Photo '{file.filename}' is empty")
        # Magic-byte allowlist first: cheapest possible rejection of anything
        # that isn't even claiming to be one of our three formats, before a
        # decoder is handed attacker-controlled bytes.
        if _sniff_extension(content) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Photo '{file.filename}' is not a supported image type (jpg/png/webp only)",
            )
        try:
            normalized.append(await normalize_inspection_photo_async(content))
        except PhotoProcessingError as exc:
            # `exc.reason` is a fixed, curated string — never a decoder message.
            raise HTTPException(
                status_code=422,
                detail=f"Photo '{file.filename}' could not be processed: {exc.reason}",
            ) from exc

    saved: list[str] = []
    try:
        for content in normalized:
            saved.append(await storage.save(content, STORED_PHOTO_EXTENSION))
    except Exception:
        for url in saved:
            filename = photo_filename_from_url(url)
            try:
                await storage.delete(filename)
            except Exception:  # noqa: BLE001 — cleanup must never mask the real error
                logger.warning("inspection_photo_partial_cleanup_failed", filename=filename)
        raise
    return saved


async def cleanup_photos(urls: list[str], storage: LocalPhotoStorage) -> None:
    """Round 13.1 orphan-cleanup guard. Call from an `except` block after
    validate_and_save_photos succeeded but the DB step that follows it
    failed — best-effort only: a cleanup failure is logged (filename only,
    never the resolved absolute path or the raw OS error string, which can
    embed it) and swallowed, never raised, so callers can safely `raise`
    the original DB/create error immediately afterward without it being
    replaced by a cleanup problem.
    """
    for url in urls:
        filename = photo_filename_from_url(url)
        try:
            await storage.delete(filename)
        except Exception:
            logger.warning("inspection_photo_cleanup_failed", filename=filename)
