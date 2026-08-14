"""GET /api/v1/records/{record_id}/photos/{photo_id} — round 13.1 scoped
photo download.

No DB fixture exists in this repo — mocks repo.get_record_scoped (the same
scope-filtered lookup GET /{record_id} already uses) and exercises the real
photo-membership + path-resolution logic directly, matching the pattern in
tests/unit/test_record_create_endpoint.py.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.api.v1.records import get_record_photo
from app.services.inspection_photos import LocalPhotoStorage, validate_and_save_photos

_MODULE = "app.api.v1.records"


def _record(**overrides):
    defaults = dict(id=uuid4(), photo_urls=[])
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db() -> MagicMock:
    return MagicMock()


async def _seed_one_photo(tmp_path: Path) -> tuple[str, str]:
    """Returns (url, filename) for a single real saved photo.

    Round 8-14A — the save path decodes and re-encodes every upload, so this
    seeds with a REAL synthetic JPEG source rather than the old magic-byte
    stub. Round 8-14A.1 — the STORED result is WebP regardless of the JPEG
    source, since validate_and_save_photos now always writes `.webp`.
    """
    import io

    from fastapi import UploadFile
    from PIL import Image

    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (90, 20, 140)).save(buffer, format="JPEG")
    jpeg = buffer.getvalue()
    photos = [
        UploadFile(file=io.BytesIO(jpeg), filename=f"{i}.jpg") for i in range(4)
    ]
    urls = await validate_and_save_photos(photos, storage)
    from app.services.inspection_photos import photo_filename_from_url

    return urls[0], photo_filename_from_url(urls[0])


async def test_scoped_user_can_download_a_photo_that_belongs_to_the_record(
    tmp_path: Path,
) -> None:
    url, filename = await _seed_one_photo(tmp_path)
    record = _record(photo_urls=[url])
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=storage)):
        response = await get_record_photo(
            record_id=record.id, scope=[], photo_id=filename, db=_mock_db(),
        )

    assert isinstance(response, FileResponse)
    # Round 8-14A.1 — a NEWLY saved photo is always WebP now.
    assert filename.endswith(".webp")
    assert response.media_type == "image/webp"


# --- round 8-14A.1: pre-existing .jpg/.png photos remain downloadable ------


@pytest.mark.parametrize(
    ("extension", "media_type"),
    [("jpg", "image/jpeg"), ("png", "image/png")],
)
async def test_legacy_photo_extensions_still_download_with_the_right_media_type(
    tmp_path: Path, extension: str, media_type: str,
) -> None:
    """A photo saved by an EARLIER round (round 8-14A wrote .jpg; pre-8-14A
    wrote .png/.webp) must still download correctly after the 8-14A.1 switch
    to WebP — this round writes NOTHING to those files, they are seeded
    directly here exactly as they'd already exist on disk."""
    filename = "0123456789abcdef0123456789abcdef." + extension
    (tmp_path / filename).write_bytes(b"not a real decode target, download never opens it")
    record = _record(photo_urls=[f"/media/inspection-photos/{filename}"])
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=storage)):
        response = await get_record_photo(
            record_id=record.id, scope=[], photo_id=filename, db=_mock_db(),
        )

    assert isinstance(response, FileResponse)
    assert response.media_type == media_type


async def test_out_of_scope_record_returns_404_not_403() -> None:
    """repo.get_record_scoped already returns None for both "doesn't exist"
    and "not in your scope" — same generic-404 principle as GET /{record_id},
    so this endpoint must not introduce a distinguishing 403 of its own."""
    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await get_record_photo(
                record_id=uuid4(), scope=[], photo_id="a" * 32 + ".jpg", db=_mock_db(),
            )

    assert exc_info.value.status_code == 404


async def test_photo_id_not_in_records_own_photo_urls_returns_404(tmp_path: Path) -> None:
    """Proves cross-record/cross-supplier filename guessing doesn't work:
    even a real, existing, correctly-shaped filename is rejected if it
    isn't listed on THIS record."""
    _, someone_elses_filename = await _seed_one_photo(tmp_path)
    record = _record(photo_urls=[])  # this record has no photos at all
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=storage)):
        with pytest.raises(HTTPException) as exc_info:
            await get_record_photo(
                record_id=record.id, scope=[], photo_id=someone_elses_filename, db=_mock_db(),
            )

    assert exc_info.value.status_code == 404


async def test_photo_listed_on_record_but_missing_on_disk_returns_404(tmp_path: Path) -> None:
    """Defense in depth: even if photo_urls somehow names a file that isn't
    actually on disk, the endpoint 404s rather than erroring."""
    fake_url = "/media/inspection-photos/" + "b" * 32 + ".jpg"
    record = _record(photo_urls=[fake_url])
    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=storage)):
        with pytest.raises(HTTPException) as exc_info:
            await get_record_photo(
                record_id=record.id, scope=[], photo_id="b" * 32 + ".jpg", db=_mock_db(),
            )

    assert exc_info.value.status_code == 404
