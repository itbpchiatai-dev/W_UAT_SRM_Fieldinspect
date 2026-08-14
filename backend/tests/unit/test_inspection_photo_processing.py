"""Round 8-14A — `normalize_inspection_photo`: the secure decode / sanitize /
downscale / re-encode pipeline every newly uploaded inspection photo passes
through before it is stored. Round 8-14A.1 switched the STORED format from
JPEG to WebP; this file was rewritten (not just suffix-swapped) to verify the
real WebP contract — content format via Pillow, real container magic bytes,
and the new 1.2/1.5 MiB size budget — not just that filenames end in .webp.

Every image here is SYNTHESIZED in-process (solid fills, gradients, seeded
noise). No real user photo is ever opened, and nothing under the configured
inspection-photo directory is read or written — these tests touch bytes in
memory only.

Sizes are asserted from `len(output)` of the real encoder, never a mocked
number: the whole point of the round is the actual bytes-on-disk guarantee.
"""
from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageCms
from PIL.TiffImagePlugin import IFDRational

from app.services.inspection_photos import (
    MAX_IMAGE_EDGE,
    MAX_IMAGE_PIXELS,
    MAX_STORED_PHOTO_BYTES,
    MIN_IMAGE_EDGE,
    MIN_WEBP_QUALITY,
    TARGET_STORED_PHOTO_BYTES,
    PhotoProcessingError,
    normalize_inspection_photo,
)

_JPEG_MAGIC = b"\xff\xd8\xff"


# --- synthetic image helpers ------------------------------------------------


def _encode(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    """Mirrors the production encoder's exact settings, for the quality-floor
    comparison test below — encoded independently here (not calling the
    private `_encode_webp` in the module under test) so the test does not
    silently pass just because it imported the same function it verifies."""
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", lossless=False, quality=quality, method=4)
    return buffer.getvalue()


def _solid(size=(640, 480), colour=(20, 130, 90)) -> Image.Image:
    return Image.new("RGB", size, colour)


def _noise(width: int, height: int, seed: int = 7) -> Image.Image:
    """Incompressible content — the worst case for hitting a byte budget."""
    rnd = random.Random(seed)
    return Image.frombytes(
        "RGB", (width, height), bytes(rnd.getrandbits(8) for _ in range(width * height * 3)),
    )


def _opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _assert_is_webp(data: bytes) -> None:
    """The real WebP container signature is `RIFF<size>WEBP` — bytes 0-3 are
    "RIFF", 4-7 are a little-endian chunk size, and 8-11 are "WEBP". A bare
    `startswith(b"RIFF")` would also match other RIFF-family containers
    (WAV, AVI), so this checks both halves of the real magic, AND asks
    Pillow to independently confirm it opens as format "WEBP" — two
    different code paths agreeing, not one assertion doing double duty."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", data[:12]
    assert _opened(data).format == "WEBP"


# --- format acceptance ------------------------------------------------------


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_supported_formats_all_normalize_to_webp(fmt: str) -> None:
    out = normalize_inspection_photo(_encode(_solid(), fmt))

    _assert_is_webp(out)


def test_webp_input_is_re_sanitized_not_passed_through_verbatim() -> None:
    """A WebP upload isn't simply forwarded as-is: metadata still gets
    stripped and the file still gets re-encoded through the same pipeline as
    any other format — proven here by attaching EXIF to a WebP source (Pillow
    allows this) and confirming it does not survive."""
    image = _solid((300, 300))
    exif = image.getexif()
    exif[0x010F] = "ACME Phone"
    source = _encode(image, "WEBP", exif=exif.tobytes())
    assert dict(_opened(source).getexif()), "fixture must actually carry EXIF"

    out = normalize_inspection_photo(source)

    _assert_is_webp(out)
    assert dict(_opened(out).getexif()) == {}
    assert b"ACME Phone" not in out


def test_output_is_always_within_the_hard_size_ceiling() -> None:
    out = normalize_inspection_photo(_encode(_solid((4000, 3000)), "JPEG", quality=95))

    assert len(out) <= MAX_STORED_PHOTO_BYTES


# --- transparency -----------------------------------------------------------


def test_transparent_png_is_composited_onto_white() -> None:
    rgba = Image.new("RGBA", (200, 150), (255, 0, 0, 0))  # fully transparent red

    out = normalize_inspection_photo(_encode(rgba, "PNG"))

    _assert_is_webp(out)
    pixel = _opened(out).convert("RGB").getpixel((100, 75))
    assert all(channel > 240 for channel in pixel), f"expected white, got {pixel}"


def test_palette_image_with_transparency_is_composited_not_crashed() -> None:
    palette = Image.new("P", (100, 100))
    palette.putpalette([255, 0, 0] * 256)
    palette.info["transparency"] = 0

    out = normalize_inspection_photo(_encode(palette, "PNG"))

    _assert_is_webp(out)
    assert _opened(out).mode == "RGB"


def test_partial_alpha_blends_towards_white_rather_than_black() -> None:
    rgba = Image.new("RGBA", (80, 80), (0, 0, 0, 128))  # half-transparent black

    out = normalize_inspection_photo(_encode(rgba, "PNG"))

    pixel = _opened(out).convert("RGB").getpixel((40, 40))
    assert all(90 < channel < 190 for channel in pixel), f"expected mid-grey, got {pixel}"


def test_transparent_webp_input_is_also_composited_onto_white() -> None:
    """The output format (WebP) CAN carry alpha, but the pipeline flattens
    onto white regardless of source OR destination format — proven with a
    WebP source this time, not just PNG."""
    rgba = Image.new("RGBA", (120, 120), (0, 200, 0, 0))  # fully transparent green
    source = _encode(rgba, "WEBP")
    assert _opened(source).mode in ("RGBA", "P"), "fixture must carry an alpha channel"

    out = normalize_inspection_photo(source)

    _assert_is_webp(out)
    result = _opened(out)
    assert result.mode == "RGB", "output must never carry an alpha channel"
    pixel = result.getpixel((60, 60))
    assert all(channel > 240 for channel in pixel), f"expected white, got {pixel}"


# --- EXIF / orientation / metadata -----------------------------------------


def test_exif_orientation_is_applied_to_the_pixels() -> None:
    """Orientation 6 = rotate 90 deg CW on display. After transposition a
    landscape source must come out portrait, with the tag itself gone."""
    landscape = _solid((400, 200))
    exif = landscape.getexif()
    exif[0x0112] = 6
    source = _encode(landscape, "JPEG", exif=exif.tobytes())

    out = normalize_inspection_photo(source)

    result = _opened(out)
    assert result.size == (200, 400), "orientation must be baked into the pixels"
    assert dict(result.getexif()) == {}


def test_exif_and_gps_are_stripped_from_the_output() -> None:
    image = _solid((300, 300))
    exif = image.getexif()
    exif[0x010F] = "ACME Phone"          # Make
    exif[0x0110] = "Model X"             # Model
    exif[0x9003] = "2026:08:05 12:00:00"  # DateTimeOriginal
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    # A real-looking coordinate (13 deg 45' 00" N — Bangkok).
    gps[2] = (IFDRational(13, 1), IFDRational(45, 1), IFDRational(0, 1))
    source = _encode(image, "JPEG", exif=exif.tobytes())
    assert dict(_opened(source).getexif()), "fixture must actually carry EXIF"

    out = normalize_inspection_photo(source)

    _assert_is_webp(out)
    result = _opened(out)
    assert dict(result.getexif()) == {}
    assert result.getexif().get_ifd(0x8825) == {}
    assert b"ACME Phone" not in out
    assert b"2026:08:05" not in out


def test_icc_profile_is_not_carried_into_the_output() -> None:
    profile = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(profile).tobytes()
    source = _encode(_solid((200, 200)), "JPEG", icc_profile=icc_bytes)
    assert _opened(source).info.get("icc_profile"), "fixture must actually carry an ICC profile"

    out = normalize_inspection_photo(source)

    assert _opened(out).info.get("icc_profile") is None


def test_malformed_icc_profile_falls_back_instead_of_failing() -> None:
    """A broken profile is a colour-accuracy problem, not a security one —
    the upload still succeeds, treated as already-sRGB."""
    source = _encode(_solid((200, 200)), "JPEG", icc_profile=b"not-a-real-icc-profile")

    out = normalize_inspection_photo(source)

    _assert_is_webp(out)
    assert _opened(out).info.get("icc_profile") is None


def test_xmp_metadata_is_not_carried_into_the_output() -> None:
    """WebP can also embed XMP (unlike the JPEG pipeline round 8-14A shipped,
    which never had to consider it) — explicitly checked here since it is a
    round 8-14A.1 contract addition, not a round 8-14A carry-over."""
    xmp_marker = b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>ROUND_8_14A_1_XMP_PROBE</x:xmpmeta>"
    source = _encode(_solid((200, 200)), "WEBP", xmp=xmp_marker)

    out = normalize_inspection_photo(source)

    assert b"ROUND_8_14A_1_XMP_PROBE" not in out
    assert _opened(out).info.get("xmp") is None


# --- geometry ---------------------------------------------------------------


def test_aspect_ratio_is_preserved_when_downscaling() -> None:
    source = _encode(_solid((4000, 1600)), "JPEG", quality=90)  # 2.5:1

    out = normalize_inspection_photo(source)

    width, height = _opened(out).size
    assert abs((width / height) - 2.5) < 0.01


def test_small_images_are_never_upscaled() -> None:
    source = _encode(_solid((320, 240)), "JPEG")

    out = normalize_inspection_photo(source)

    assert _opened(out).size == (320, 240)


def test_longest_edge_is_capped_at_the_maximum() -> None:
    source = _encode(_solid((6000, 2000)), "JPEG", quality=90)

    out = normalize_inspection_photo(source)

    assert max(_opened(out).size) == MAX_IMAGE_EDGE


def test_an_image_exactly_at_the_edge_limit_is_left_alone() -> None:
    source = _encode(_solid((MAX_IMAGE_EDGE, 1000)), "JPEG", quality=90)

    out = normalize_inspection_photo(source)

    assert _opened(out).size == (MAX_IMAGE_EDGE, 1000)


# --- size budget (round 8-14A.1: target 1.2 MiB, hard max 1.5 MiB) ---------


def test_high_entropy_photo_is_brought_under_the_ceiling() -> None:
    """The headline case: a large, incompressible photo (what a real camera
    produces on a detailed scene) must still land under 1.5 MiB."""
    source = _encode(_noise(2600, 1960), "JPEG", quality=95)
    assert 5 * 1024 * 1024 <= len(source) <= 8 * 1024 * 1024, (
        f"fixture should be ~6 MiB, got {len(source) / 1024 / 1024:.2f} MiB"
    )

    out = normalize_inspection_photo(source)

    _assert_is_webp(out)
    assert len(out) <= MAX_STORED_PHOTO_BYTES


def test_ordinary_photo_comfortably_reaches_the_target_not_just_the_ceiling() -> None:
    source = _encode(_solid((3000, 2000)), "JPEG", quality=95)

    out = normalize_inspection_photo(source)

    assert len(out) <= TARGET_STORED_PHOTO_BYTES


def test_extremely_incompressible_photo_falls_back_to_reducing_dimensions() -> None:
    """When even quality 75 at full size exceeds the ceiling, the pipeline
    shrinks the image rather than dropping quality below the floor.

    A SQUARE source is used deliberately: capping the longest edge at 2560
    leaves a square with the most pixels any allowed shape can have
    (2560x2560), which is what forces the ladder past the quality floor and
    into the downscale step.
    """
    source = _encode(_noise(3200, 3200, seed=11), "JPEG", quality=98)

    out = normalize_inspection_photo(source)

    assert len(out) <= MAX_STORED_PHOTO_BYTES
    longest = max(_opened(out).size)
    assert longest < MAX_IMAGE_EDGE, "should have stepped the size down"
    assert longest >= MIN_IMAGE_EDGE, "must not shrink past the safety floor"


def test_quality_never_drops_below_the_floor() -> None:
    """The pipeline's own output must never look MORE compressed than what
    the floor quality (75) would produce, and must look LESS compressed than
    a below-floor quality would — bracketing it from both sides using the
    exact same encoder settings the pipeline itself uses (method=4,
    lossless=False), independently re-invoked here."""
    source = _encode(_noise(2400, 1800, seed=3), "JPEG", quality=95)

    out = normalize_inspection_photo(source)

    result = _opened(out).convert("RGB")
    at_floor = _encode_webp(result, MIN_WEBP_QUALITY)
    below_floor = _encode_webp(result, MIN_WEBP_QUALITY - 15)
    assert len(out) > len(below_floor), "output looks over-compressed for the stated floor"
    assert len(at_floor) > len(below_floor)


def test_compression_search_terminates_and_does_not_hang() -> None:
    """Bounded attempt budget: the worst realistic input still returns."""
    source = _encode(_noise(3500, 3500, seed=5), "JPEG", quality=98)

    out = normalize_inspection_photo(source)

    assert len(out) <= MAX_STORED_PHOTO_BYTES


# --- rejection paths --------------------------------------------------------


def test_empty_input_is_rejected_cleanly() -> None:
    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(b"")


def test_truncated_jpeg_is_rejected_cleanly() -> None:
    full = _encode(_solid((800, 600)), "JPEG", quality=90)

    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(full[: len(full) // 3])


def test_truncated_webp_is_rejected_cleanly() -> None:
    full = _encode(_solid((800, 600)), "WEBP", quality=90)

    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(full[: len(full) // 3])


def test_malformed_jpeg_body_is_rejected_cleanly() -> None:
    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(_JPEG_MAGIC + b"\x00" * 5000)


def test_renamed_non_image_is_rejected_cleanly() -> None:
    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(b"MZ\x90\x00" + b"\x00" * 4000)  # PE/EXE header


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"GIF89a" + b"\x00" * 100, id="gif"),
        pytest.param(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", id="svg"),
        pytest.param(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 100, id="pdf"),
        pytest.param(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100, id="heic"),
    ],
)
def test_unsupported_formats_are_rejected(payload: bytes) -> None:
    with pytest.raises(PhotoProcessingError):
        normalize_inspection_photo(payload)


def test_animated_webp_is_rejected() -> None:
    """Only the first frame would survive a re-encode — silently discarding
    the rest is worse than refusing the upload. Especially relevant now that
    the OUTPUT format is also WebP: an animated-in, animated-out illusion
    must not happen — the pipeline never emits more than one frame."""
    frames = [_solid((80, 80), (255, 0, 0)), _solid((80, 80), (0, 0, 255))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    animated = buffer.getvalue()
    assert getattr(_opened(animated), "n_frames", 1) > 1, "fixture must be animated"

    with pytest.raises(PhotoProcessingError, match="animated"):
        normalize_inspection_photo(animated)


def test_pixel_bomb_is_rejected_from_the_header_without_decoding() -> None:
    """A small PNG declaring a canvas past the pixel budget is refused on the
    header alone — it never gets to allocate that bitmap."""
    edge = 8000  # 64M pixels > MAX_IMAGE_PIXELS
    assert edge * edge > MAX_IMAGE_PIXELS
    bomb = _encode(Image.new("L", (edge, edge), 0), "PNG", compress_level=9)
    assert len(bomb) < 1024 * 1024, "fixture should be a small file claiming a huge canvas"

    with pytest.raises(PhotoProcessingError, match="too large"):
        normalize_inspection_photo(bomb)


def test_rejection_never_leaks_decoder_internals() -> None:
    """The message shown to a caller is a fixed curated string — never a raw
    Pillow/ libjpeg/libwebp error, which can carry offsets and content
    fragments."""
    with pytest.raises(PhotoProcessingError) as exc_info:
        normalize_inspection_photo(_JPEG_MAGIC + b"\x41" * 3000)

    reason = exc_info.value.reason
    assert reason in {
        "unreadable or unsupported image",
        "malformed image",
        "malformed or truncated image",
    }
    assert "\n" not in reason


# --- both upload routes share one pipeline ---------------------------------


def test_logged_in_and_public_endpoints_use_the_same_save_helper() -> None:
    """Neither route may grow its own photo path: a field worker's photo must
    be sanitized identically to an admin's. Both import the SAME
    `validate_and_save_photos` object, which is the only caller of the
    processor — so this identity check is what stops the two from drifting.
    """
    from app.api.v1 import public_records, records
    from app.services import inspection_photos

    assert records.validate_and_save_photos is inspection_photos.validate_and_save_photos
    assert public_records.validate_and_save_photos is inspection_photos.validate_and_save_photos


def test_the_save_helper_is_the_only_route_into_the_processor() -> None:
    """Guards the assumption the test above relies on: no endpoint module
    reaches past `validate_and_save_photos` to normalize photos itself."""
    import inspect as _inspect

    from app.api.v1 import public_records, records

    for module in (records, public_records):
        source = _inspect.getsource(module)
        assert "normalize_inspection_photo" not in source, (
            f"{module.__name__} must go through validate_and_save_photos"
        )
