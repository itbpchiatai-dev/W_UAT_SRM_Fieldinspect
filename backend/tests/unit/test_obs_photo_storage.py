"""OBSPhotoStorage + get_photo_storage factory — round 8-16B.

Tests cover:
  - OBSPhotoStorage.save  → uploads to correct OBS key via putContent, returns the key
  - OBSPhotoStorage.delete → calls deleteObject with the stored URL as objectKey
  - OBSPhotoStorage.get_presigned_url → calls createSignedUrl correctly
  - get_photo_storage factory returns OBSPhotoStorage when env is configured
  - get_photo_storage factory returns LocalPhotoStorage when env is absent
  - Download route returns RedirectResponse for an OBS key (no leading slash)
  - Download route returns FileResponse for a legacy local URL (leading slash)

All OBS SDK I/O is mocked — no real network calls.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

_BUCKET = "bk-srm"
_ENDPOINT = "obs.ap-southeast-2.myhuaweicloud.com"
_ACCESS_KEY = "TESTKEY"
_SECRET_KEY = "TESTSECRET"
_ENV_PREFIX = "UAT"
_PLOT_CODE = "ABC-001"


def _make_obs_storage(plot_code: str = _PLOT_CODE):
    """Create an OBSPhotoStorage with a mocked OBS SDK client."""
    from app.services.inspection_photos import OBSPhotoStorage

    storage = OBSPhotoStorage.__new__(OBSPhotoStorage)
    storage._bucket = _BUCKET
    storage._env_prefix = _ENV_PREFIX
    storage._plot_code = plot_code
    storage._client = MagicMock()
    return storage


# ---------------------------------------------------------------------------
# OBSPhotoStorage.save
# ---------------------------------------------------------------------------

async def test_obs_save_returns_obs_key_with_correct_format() -> None:
    storage = _make_obs_storage()
    storage._client.putContent = MagicMock(return_value=SimpleNamespace(status=200))

    key = await storage.save(b"fake-webp-bytes", "webp")

    pattern = rf"^{_ENV_PREFIX}/{_PLOT_CODE}/[0-9a-f]{{32}}\.webp$"
    assert re.match(pattern, key), f"Key '{key}' did not match expected pattern"


async def test_obs_save_calls_putcontent_with_bucket_and_key() -> None:
    storage = _make_obs_storage()
    storage._client.putContent = MagicMock(return_value=SimpleNamespace(status=200))

    key = await storage.save(b"fake-webp-bytes", "webp")

    call_kwargs = storage._client.putContent.call_args.kwargs
    assert call_kwargs["bucketName"] == _BUCKET
    assert call_kwargs["objectKey"] == key


async def test_obs_save_plot_code_is_baked_into_key() -> None:
    storage = _make_obs_storage(plot_code="XY-999")
    storage._client.putContent = MagicMock(return_value=SimpleNamespace(status=200))

    key = await storage.save(b"bytes", "webp")

    assert key.startswith("UAT/XY-999/")


async def test_obs_save_raises_on_non_200_status() -> None:
    storage = _make_obs_storage()
    storage._client.putContent = MagicMock(
        return_value=SimpleNamespace(status=403, errorMessage="Forbidden")
    )

    with pytest.raises(OSError, match="OBS putContent failed"):
        await storage.save(b"bytes", "webp")


# ---------------------------------------------------------------------------
# OBSPhotoStorage.delete
# ---------------------------------------------------------------------------

async def test_obs_delete_calls_deleteobject_with_stored_url_as_key() -> None:
    storage = _make_obs_storage()
    stored_url = "UAT/ABC-001/deadbeef12345678abcdef1234567890.webp"
    storage._client.deleteObject = MagicMock()

    await storage.delete(stored_url)

    storage._client.deleteObject.assert_called_once_with(
        bucketName=_BUCKET,
        objectKey=stored_url,
    )


async def test_obs_delete_swallows_exception_without_raising() -> None:
    storage = _make_obs_storage()
    storage._client.deleteObject = MagicMock(side_effect=RuntimeError("network error"))

    await storage.delete("UAT/ABC-001/somekey.webp")  # must not raise


# ---------------------------------------------------------------------------
# OBSPhotoStorage.get_presigned_url
# ---------------------------------------------------------------------------

def test_obs_get_presigned_url_calls_createsignedurl() -> None:
    storage = _make_obs_storage()
    presigned = "https://bk-srm.obs.ap-southeast-2.myhuaweicloud.com/UAT/ABC-001/abc.webp?Signature=abc"
    storage._client.createSignedUrl = MagicMock(
        return_value={"signedUrl": presigned}
    )
    key = "UAT/ABC-001/deadbeef12345678abcdef1234567890.webp"

    with patch(
        "app.core.config.get_settings",
        return_value=SimpleNamespace(OBS_PRESIGNED_EXPIRY_SECONDS=900),
    ):
        url = storage.get_presigned_url(key)

    storage._client.createSignedUrl.assert_called_once_with(
        method="GET",
        bucketName=_BUCKET,
        objectKey=key,
        expires=900,
    )
    assert url == presigned


# ---------------------------------------------------------------------------
# get_photo_storage factory
# ---------------------------------------------------------------------------

def test_get_photo_storage_returns_obs_when_obs_endpoint_configured() -> None:
    from app.services.inspection_photos import OBSPhotoStorage

    with patch("app.core.config.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            OBS_ENDPOINT="obs.ap-southeast-2.myhuaweicloud.com",
            OBS_ACCESS_KEY_ID="KEYID",
            OBS_SECRET_ACCESS_KEY="SECRET",
            OBS_BUCKET_NAME=_BUCKET,
            OBS_ENV_PREFIX="UAT",
            OBS_TIMEOUT_SECONDS=30,
            INSPECTION_PHOTOS_DIR="var/inspection-photos",
            INSPECTION_PHOTOS_URL_PREFIX="/media/inspection-photos",
        )
        with patch("app.services.inspection_photos.OBSPhotoStorage.__init__", return_value=None):
            from app.services.inspection_photos import get_photo_storage

            storage = get_photo_storage(plot_code="ABC-001")

    assert isinstance(storage, OBSPhotoStorage)


def test_get_photo_storage_returns_local_when_obs_not_configured() -> None:
    from app.services.inspection_photos import LocalPhotoStorage, get_photo_storage

    with patch("app.core.config.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            OBS_ENDPOINT="",
            OBS_ACCESS_KEY_ID="",
            INSPECTION_PHOTOS_DIR="var/inspection-photos",
            INSPECTION_PHOTOS_URL_PREFIX="/media/inspection-photos",
        )
        storage = get_photo_storage(plot_code="ABC-001")

    assert isinstance(storage, LocalPhotoStorage)


# ---------------------------------------------------------------------------
# Download route: OBS key → RedirectResponse
# ---------------------------------------------------------------------------

_MODULE = "app.api.v1.records"


def _record_with_obs_url(obs_key: str):
    return SimpleNamespace(id=uuid4(), photo_urls=[obs_key])


def _record_with_local_url(local_url: str):
    return SimpleNamespace(id=uuid4(), photo_urls=[local_url])


def _mock_db():
    return MagicMock()


async def test_download_route_redirects_302_for_obs_key() -> None:
    from app.api.v1.records import get_record_photo

    obs_key = "UAT/ABC-001/deadbeef12345678abcdef1234567890.webp"
    filename = "deadbeef12345678abcdef1234567890.webp"
    record = _record_with_obs_url(obs_key)
    presigned = "https://obs.example.com/presigned?Signature=abc"

    obs_storage = _make_obs_storage()
    obs_storage.get_presigned_url = MagicMock(return_value=presigned)

    with (
        patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)),
        patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=obs_storage)),
    ):
        response = await get_record_photo(
            record_id=record.id, scope=[], photo_id=filename, db=_mock_db()
        )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    obs_storage.get_presigned_url.assert_called_once_with(obs_key)


async def test_download_route_returns_file_response_for_local_url(
    tmp_path: Path,
) -> None:
    from app.api.v1.records import get_record_photo
    from app.services.inspection_photos import LocalPhotoStorage

    import io

    from fastapi import UploadFile
    from PIL import Image

    storage = LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buf, format="JPEG")
    from app.services.inspection_photos import validate_and_save_photos

    urls = await validate_and_save_photos(
        [UploadFile(file=io.BytesIO(buf.getvalue()), filename="p.jpg")], storage
    )
    local_url = urls[0]
    from app.services.inspection_photos import photo_filename_from_url

    filename = photo_filename_from_url(local_url)
    record = _record_with_local_url(local_url)

    with (
        patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)),
        patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=storage)),
    ):
        response = await get_record_photo(
            record_id=record.id, scope=[], photo_id=filename, db=_mock_db()
        )

    assert isinstance(response, FileResponse)


async def test_download_route_404_when_obs_storage_unavailable_for_obs_key() -> None:
    """If the stored URL is an OBS key but get_photo_storage returns LocalPhotoStorage
    (misconfiguration), the route must return 404 rather than crashing."""
    from app.api.v1.records import get_record_photo
    from app.services.inspection_photos import LocalPhotoStorage

    obs_key = "UAT/ABC-001/deadbeef12345678abcdef1234567890.webp"
    filename = "deadbeef12345678abcdef1234567890.webp"
    record = _record_with_obs_url(obs_key)
    local_storage = LocalPhotoStorage(root=Path("/tmp/nope"), url_prefix="/media")

    with (
        patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)),
        patch(f"{_MODULE}.get_photo_storage", MagicMock(return_value=local_storage)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_record_photo(
                record_id=record.id, scope=[], photo_id=filename, db=_mock_db()
            )

    assert exc_info.value.status_code == 404
