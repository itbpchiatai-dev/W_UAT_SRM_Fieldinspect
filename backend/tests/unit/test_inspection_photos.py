"""app.services.inspection_photos — round 13 photo validation + storage,
round 8-14A normalize-before-save, round 8-14A.1 switched stored output to
WebP.

No DB involved here — pure service-layer tests exercising real UploadFile
objects (in-memory BytesIO) against the real validation/storage code, no
mocks needed since there's nothing external to fake.

Round 8-14A: the fixtures below are REAL encoded images, not magic-byte
stubs. They used to be `b"\\xff\\xd8\\xff" + zeros`, which satisfied the old
"sniff the first bytes" contract; the service now genuinely decodes every
upload, so a stub would (correctly) be rejected as malformed. Every image
here is synthesized in-process — no real user photo is ever read.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.services.inspection_photos import (
    MAX_PHOTO_COUNT,
    MAX_PHOTO_UPLOAD_BYTES,
    PHOTO_FILENAME_PATTERN,
    LocalPhotoStorage,
    cleanup_photos,
    media_type_for,
    photo_filename_from_url,
    validate_and_save_photos,
)


def _encode(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _solid(size: tuple[int, int] = (120, 90), colour: tuple[int, int, int] = (12, 140, 90)):
    return Image.new("RGB", size, colour)


_JPEG = _encode(_solid(), "JPEG")
_PNG = _encode(_solid(), "PNG")
_WEBP = _encode(_solid(), "WEBP")
_NOT_AN_IMAGE = b"this is definitely not an image" * 10


def _upload(content: bytes, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _four_valid_photos() -> list[UploadFile]:
    return [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(_JPEG, "d.jpg")]


async def test_saves_four_valid_photos_and_returns_four_urls(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    urls = await validate_and_save_photos(_four_valid_photos(), storage)

    assert len(urls) == 4
    saved_files = list(tmp_path.iterdir())
    assert len(saved_files) == 4
    for url in urls:
        assert url.startswith("/media/inspection-photos/")


async def test_any_count_from_one_to_max_is_accepted(tmp_path: Path) -> None:
    """Photos are optional now (the old exact-4 rule is gone) — this
    multipart path accepts 1..MAX_PHOTO_COUNT; zero-photo submits use the
    plain JSON endpoints and never reach here."""
    for count in (1, MAX_PHOTO_COUNT):
        root = tmp_path / f"n{count}"
        storage = LocalPhotoStorage(root=root, url_prefix="/media/inspection-photos")

        urls = await validate_and_save_photos([_upload(_JPEG) for _ in range(count)], storage)

        assert len(urls) == count
        assert len(list(root.iterdir())) == count


async def test_zero_photos_is_rejected(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos([], storage)

    assert exc_info.value.status_code == 422


async def test_more_than_max_photos_is_rejected(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    photos = [_upload(_JPEG) for _ in range(MAX_PHOTO_COUNT + 1)]

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos(photos, storage)

    assert exc_info.value.status_code == 422


async def test_non_image_file_is_rejected(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    photos = [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(_NOT_AN_IMAGE, "d.txt")]

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos(photos, storage)

    assert exc_info.value.status_code == 400
    # Nothing partially saved — validation happens before any save() call.
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_oversized_photo_is_rejected(tmp_path: Path) -> None:
    """Round 8-14A raised the INPUT cap to 15 MiB; anything past it is still a
    413, and it is refused on the byte count alone (never decoded)."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    oversized = b"\xff\xd8\xff" + b"a" * MAX_PHOTO_UPLOAD_BYTES
    photos = [_upload(oversized), _upload(_PNG), _upload(_WEBP), _upload(_JPEG, "d.jpg")]

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos(photos, storage)

    assert exc_info.value.status_code == 413
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_empty_file_is_rejected(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    photos = [_upload(b""), _upload(_PNG), _upload(_WEBP), _upload(_JPEG, "d.jpg")]

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos(photos, storage)

    assert exc_info.value.status_code == 422


async def test_malicious_filename_never_reaches_the_saved_path(tmp_path: Path) -> None:
    """Server always generates the on-disk filename (uuid4) — a client
    sending a path-traversal-shaped `filename` (e.g. "../../../etc/passwd")
    has no way to influence where the file lands, since that value is never
    read when building the save path."""
    storage_root = tmp_path / "photos"
    storage = LocalPhotoStorage(root=storage_root, url_prefix="/media/inspection-photos")
    photos = [
        _upload(_JPEG, filename="../../../etc/passwd"),
        _upload(_PNG, filename="..\\..\\windows\\win.ini"),
        _upload(_WEBP),
        _upload(_JPEG, "d.jpg"),
    ]

    urls = await validate_and_save_photos(photos, storage)

    saved_files = list(storage_root.iterdir())
    assert len(saved_files) == 4
    for f in saved_files:
        # Resolved path must stay inside storage_root — no ".." escape.
        assert f.resolve().parent == storage_root.resolve()
        assert ".." not in f.name
    for url in urls:
        assert url.startswith("/media/inspection-photos/")
        assert ".." not in url


# --- round 13.1: scoped download + orphan cleanup ---------------------------

async def test_resolve_existing_path_finds_a_saved_photo(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    urls = await validate_and_save_photos(_four_valid_photos(), storage)
    filename = photo_filename_from_url(urls[0])

    resolved = storage.resolve_existing_path(filename)

    assert resolved.exists()
    assert resolved.parent.resolve() == tmp_path.resolve()


def test_resolve_existing_path_rejects_traversal_shaped_filename(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with pytest.raises(FileNotFoundError):
        storage.resolve_existing_path("../../../etc/passwd")


def test_resolve_existing_path_rejects_nonexistent_filename(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with pytest.raises(FileNotFoundError):
        storage.resolve_existing_path("00000000000000000000000000000000.jpg")


async def test_delete_removes_a_saved_photo(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    urls = await validate_and_save_photos(_four_valid_photos(), storage)
    filename = photo_filename_from_url(urls[0])

    await storage.delete(filename)

    with pytest.raises(FileNotFoundError):
        storage.resolve_existing_path(filename)
    # The other three are untouched.
    assert len(list(tmp_path.iterdir())) == 3


async def test_delete_is_a_silent_no_op_for_traversal_shaped_or_missing_filenames(
    tmp_path: Path,
) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    await storage.delete("../../../etc/passwd")
    await storage.delete("00000000000000000000000000000000.jpg")  # never existed


async def test_cleanup_photos_deletes_every_saved_url(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    urls = await validate_and_save_photos(_four_valid_photos(), storage)

    await cleanup_photos(urls, storage)

    assert list(tmp_path.iterdir()) == []


async def test_cleanup_photos_swallows_delete_errors_and_logs_no_absolute_path(
    tmp_path: Path,
) -> None:
    """A cleanup failure must never raise (the caller relies on this to
    re-raise the ORIGINAL db error untouched) and the log call must never
    be given the resolved absolute path or the raw OS exception (which can
    embed that path) — only the bare filename."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    urls = await validate_and_save_photos(_four_valid_photos(), storage)

    with patch.object(storage, "delete", AsyncMock(side_effect=OSError(f"boom at {tmp_path}"))), \
         patch("app.services.inspection_photos.logger") as mocked_logger:
        await cleanup_photos(urls, storage)  # must not raise

    assert mocked_logger.warning.called
    for call in mocked_logger.warning.call_args_list:
        args, kwargs = call
        serialized = " ".join(str(a) for a in args) + " ".join(f"{k}={v}" for k, v in kwargs.items())
        assert str(tmp_path) not in serialized
        assert "boom" not in serialized


def test_photo_filename_from_url_strips_the_prefix() -> None:
    assert photo_filename_from_url("/media/inspection-photos/abc123.jpg") == "abc123.jpg"


def test_media_type_for_known_extensions() -> None:
    assert media_type_for("x.jpg") == "image/jpeg"
    assert media_type_for("x.png") == "image/png"
    assert media_type_for("x.webp") == "image/webp"


def test_photo_filename_pattern_accepts_only_generated_shape() -> None:
    valid = "0123456789abcdef0123456789abcdef.jpg"
    assert re.fullmatch(PHOTO_FILENAME_PATTERN, valid)

    for bad in (
        "../../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "0123456789abcdef0123456789abcdef.exe",
        "0123456789abcdef0123456789abcdef",
        "not-even-hex-but-32-charsxxxxxxx.jpg",
        "0123456789abcdef0123456789abcdef.jpg/../x",
    ):
        assert re.fullmatch(PHOTO_FILENAME_PATTERN, bad) is None


# --- round 8-14A: normalize-before-save flow --------------------------------


async def test_every_saved_photo_is_webp_regardless_of_input_format(tmp_path: Path) -> None:
    """A JPEG, a PNG, and a WebP upload all land on disk as WebP (round
    8-14A.1) — the stored format is decided by the server, not by what the
    client sent. Checked via the real container magic bytes AND by asking
    Pillow to open each saved file and report its format, not just the
    filename suffix."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    urls = await validate_and_save_photos(
        [_upload(_JPEG, "a.jpg"), _upload(_PNG, "b.png"), _upload(_WEBP, "c.webp")], storage,
    )

    assert all(url.endswith(".webp") for url in urls)
    for path in tmp_path.iterdir():
        assert path.suffix == ".webp"
        content = path.read_bytes()
        assert content[:4] == b"RIFF" and content[8:12] == b"WEBP", content[:12]
        assert Image.open(io.BytesIO(content)).format == "WEBP"


async def test_saved_url_order_matches_input_order(tmp_path: Path) -> None:
    """Photo slots are positional in the UI (each slot means a different
    subject), so a reordered photo_urls would mislabel every one of them."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    # Distinct solid colours so each saved file is identifiable by content.
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    uploads = [_upload(_encode(_solid((60, 60), c), "JPEG", quality=95)) for c in colours]

    urls = await validate_and_save_photos(uploads, storage)

    assert len(urls) == 3
    for url, expected in zip(urls, colours):
        saved = Image.open(io.BytesIO((tmp_path / photo_filename_from_url(url)).read_bytes()))
        actual = saved.convert("RGB").getpixel((30, 30))
        # Lossy WebP — compare by which channel dominates, not exact bytes.
        assert actual.index(max(actual)) == expected.index(max(expected))


async def test_stored_filename_never_derives_from_the_client_filename(tmp_path: Path) -> None:
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    urls = await validate_and_save_photos(
        [_upload(_JPEG, "my-holiday-photo-2026.jpg")], storage,
    )

    filename = photo_filename_from_url(urls[0])
    assert "holiday" not in filename
    assert re.fullmatch(PHOTO_FILENAME_PATTERN, filename)


async def test_a_corrupt_photo_late_in_the_batch_saves_nothing_at_all(tmp_path: Path) -> None:
    """Normalization of EVERY photo happens before ANY save, so a batch whose
    last photo is corrupt leaves no orphans from the first three."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    truncated = _JPEG[: len(_JPEG) // 3]
    photos = [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(truncated, "d.jpg")]

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save_photos(photos, storage)

    assert exc_info.value.status_code == 422
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_partial_save_failure_removes_the_photos_already_written(tmp_path: Path) -> None:
    """If save #3 of 4 fails, the two files already on disk are deleted and
    the ORIGINAL error is re-raised — the caller's own cleanup can't help
    here because it never receives any URLs from a raising call."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    real_save = storage.save
    calls = {"n": 0}

    async def flaky_save(content: bytes, extension: str) -> str:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return await real_save(content, extension)

    with patch.object(storage, "save", side_effect=flaky_save):
        with pytest.raises(OSError, match="disk full"):
            await validate_and_save_photos(_four_valid_photos(), storage)

    assert list(tmp_path.iterdir()) == []


async def test_partial_save_cleanup_failure_still_reraises_the_original_error(
    tmp_path: Path,
) -> None:
    """A failure while cleaning up must never replace the real error, and the
    log line must carry only the generated filename."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    real_save = storage.save
    calls = {"n": 0}

    async def flaky_save(content: bytes, extension: str) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return await real_save(content, extension)

    with patch.object(storage, "save", side_effect=flaky_save), \
         patch.object(storage, "delete", AsyncMock(side_effect=OSError(f"nope {tmp_path}"))), \
         patch("app.services.inspection_photos.logger") as mocked_logger:
        with pytest.raises(OSError, match="disk full"):
            await validate_and_save_photos(_four_valid_photos(), storage)

    assert mocked_logger.warning.called
    for call in mocked_logger.warning.call_args_list:
        args, kwargs = call
        serialized = " ".join(str(a) for a in args) + " ".join(f"{k}={v}" for k, v in kwargs.items())
        assert str(tmp_path) not in serialized
        assert "nope" not in serialized


async def test_processing_runs_off_the_event_loop_via_to_thread(tmp_path: Path) -> None:
    """CPU-bound Pillow work must not run inline on the event loop."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with patch(
        "app.services.inspection_photos.asyncio.to_thread",
        wraps=__import__("asyncio").to_thread,
    ) as spy:
        await validate_and_save_photos([_upload(_JPEG), _upload(_PNG)], storage)

    normalize_calls = [
        c for c in spy.call_args_list
        if getattr(c.args[0], "__name__", "") == "normalize_inspection_photo"
    ]
    assert len(normalize_calls) == 2


async def test_concurrent_processing_is_bounded_by_the_semaphore(tmp_path: Path) -> None:
    """More than MAX_CONCURRENT_IMAGE_JOBS photos may be uploaded at once, but
    never more than that many may be decoding simultaneously."""
    import asyncio as _asyncio

    from app.services.inspection_photos import (
        MAX_CONCURRENT_IMAGE_JOBS,
        normalize_inspection_photo_async,
    )

    live = {"now": 0, "peak": 0}
    real = _asyncio.to_thread

    async def tracking_to_thread(fn, *args, **kwargs):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        try:
            await _asyncio.sleep(0.01)  # hold the slot so overlap is observable
            return await real(fn, *args, **kwargs)
        finally:
            live["now"] -= 1

    with patch("app.services.inspection_photos.asyncio.to_thread", tracking_to_thread):
        await _asyncio.gather(*(normalize_inspection_photo_async(_JPEG) for _ in range(6)))

    assert live["peak"] <= MAX_CONCURRENT_IMAGE_JOBS


async def test_historical_jpg_png_and_webp_filenames_remain_downloadable(tmp_path: Path) -> None:
    """New writes are always .webp (round 8-14A.1), but photos stored by
    earlier rounds keep their original extension: .png/.webp from before
    round 8-14A, AND .jpg from round 8-14A itself. The filename gate and
    media-type map must keep accepting all three, or every pre-8-14A.1 photo
    would 422 on download — this is a real regression risk since it is
    exactly what round 8-14A's own switch (JPEG -> WebP) makes newly
    "historical"."""
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    for legacy in ("0123456789abcdef0123456789abcdef.png",
                   "fedcba9876543210fedcba9876543210.webp",
                   "aabbccddeeff00112233445566778899.jpg"):
        assert re.fullmatch(PHOTO_FILENAME_PATTERN, legacy)
        (tmp_path / legacy).write_bytes(_PNG)
        assert storage.resolve_existing_path(legacy).is_file()
    assert media_type_for("x.png") == "image/png"
    assert media_type_for("x.webp") == "image/webp"
    assert media_type_for("x.jpg") == "image/jpeg"
