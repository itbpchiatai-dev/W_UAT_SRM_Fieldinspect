"""Auto Plot Code — the ONE place that formats a server-generated plot code
and normalizes its components (round B). No endpoint / import / repository
duplicates this logic; they all call these helpers.

Auto Plot Code format V1:

    {supplierCode}-{YYMM}-{running}

  e.g. JPS-2605-001      (supplier JPS, May 2026, the 1st plot of that month)
       JPS-2605-038
       JPS-2611-001      (a new month restarts the running number)

The month is YYMM in the CHRISTIAN era, two digits each — 2605 = May 2026 —
chosen to match the Auto Lot examples (lot_number.py renders "2605-SUP010-…"
from a cycle label users already write that way), so the two identifiers a
plot carries never disagree about what "2605" means.

Where the month comes from is the CALLER's decision, not this module's: a plot
created together with its first cycle uses that cycle's planting date, and a
plot created on its own falls back to today in Asia/Bangkok. Both arrive here
as a plain date — see month_stamp().

The running number counts within a (supplier, YYMM) series, ACROSS plots, and
restarts at 1 for every new series. It is allocated at INSERT time under the
supplier's series, with a partial unique index as the concurrency backstop
(migration 0053) — exactly the shape lot_number.py's V2 running number uses.

This module is deliberately a sibling of lot_number.py rather than a
generalisation of it: the two identifiers are independent (different
components, different series, different lifetimes) and merging them would
couple two things that only happen to look alike today.
"""
from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

# plot_code is VARCHAR(50) (app/db/models/plot.py); a generated code must fit
# that column.
MAX_PLOT_CODE_LENGTH = 50

# Minimum zero-pad width for the running number (3 digits: 001..999, then
# 1000+). Matches lot_number._RUNNING_MIN_WIDTH so the two identifiers read
# alike; str.zfill never truncates, so 1000 renders as "1000", not "000".
_RUNNING_MIN_WIDTH = 3

# plot_code_series_key is VARCHAR(255) (migration 0053).
MAX_PLOT_CODE_SERIES_KEY_LENGTH = 255

# Version stamp on the series-key encoding. Bump ONLY together with a migration
# that rewrites stored keys — an unversioned change would let a new key
# silently coexist with an old one for the same logical series and restart its
# running sequence. (Same rule as lot_number._SERIES_KEY_SCHEME.)
_SERIES_KEY_SCHEME = "pcv1"

# The supplier code is embedded verbatim into a plot code, and a plot code is
# used as an object-storage folder name for that plot's inspection photos
# (services/inspection_photos.py keys objects by plot_code). A space or a Thai
# character there is exactly the problem this round exists to stop, so a
# supplier code that cannot be embedded safely is refused with a message naming
# the supplier — never silently sanitised into something that no longer matches
# the supplier's real code.
_SAFE_SUPPLIER_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")

# Round B — the fallback month is "today" as a Thai user means it, not as UTC
# means it: at 03:00 Bangkok on the 1st, UTC is still the previous month, and a
# code stamped 2604 for a plot everyone created in May would be wrong on paper
# forever. Mirrors app/api/v1/public_inspection_access.py's _BANGKOK_TZ.
_BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


class PlotCodeTooLongError(ValueError):
    """A generated plot code would exceed MAX_PLOT_CODE_LENGTH. Raised by
    format_auto_plot_code so the caller can surface a clean 422 (never a 500,
    never a silently truncated code that would collide with its neighbours)."""


class PlotCodeSupplierCodeUnusableError(ValueError):
    """The supplier's own code cannot be embedded in a plot code (blank, or
    carrying characters that must not reach a storage path).

    Carries the offending supplier code because it is not user-submitted data:
    it is stored master data the admin can go and fix, and naming it is the
    whole point of the message."""

    def __init__(self, supplier_code: str | None) -> None:
        self.supplier_code = supplier_code
        shown = supplier_code if supplier_code else "(ว่าง)"
        super().__init__(
            f"Supplier code {shown!r} cannot be used to build a plot code; "
            "it must be A-Z, 0-9, '-' or '_' and start with a letter or digit."
        )


def normalize_supplier_code_for_plot_code(value: str | None) -> str:
    """Trim + upper-case a supplier code for embedding in a plot code.

    Upper-casing matches plot_repository.create_plot, which stores every
    plot_code upper-cased: normalizing here means the code we RENDER and the
    code we later look up are the same string, and that the series key counts
    "jps" and "JPS" as one series rather than two.

    Raises PlotCodeSupplierCodeUnusableError rather than returning None — a
    caller that got here has already decided to generate a code, and there is
    no safe substitute for a missing supplier code."""
    normalized = (value or "").strip().upper()
    if not _SAFE_SUPPLIER_CODE.match(normalized):
        raise PlotCodeSupplierCodeUnusableError(value)
    return normalized


def month_stamp(on: datetime.date) -> str:
    """YYMM in the Christian era for `on` — 2026-05-17 -> "2605".

    Takes a plain date so the caller owns the "which date?" decision
    (planting date vs creation date) and this stays a pure formatter."""
    return f"{on.year % 100:02d}{on.month:02d}"


def today_in_bangkok() -> datetime.date:
    """The fallback month source: today's date in Asia/Bangkok. Used when the
    plot is created without a planting date to borrow the month from."""
    return datetime.datetime.now(_BANGKOK_TZ).date()


def build_plot_code_series_key(supplier_code: str, month: str) -> str:
    """The INTERNAL series identity an Auto Plot Code running number counts
    within: supplier + YYMM.

    Length-prefixed rather than delimiter-joined, for the same reason
    lot_number.build_auto_lot_series_key is (round 8-12A.1): a supplier code
    may contain "-", so a plain join could let two distinct pairs render one
    key. The month is fixed-width, but encoding both components uniformly
    keeps the scheme obviously unambiguous:

        ("JPS", "2605")   -> "pcv1|3:JPS|4:2605"

    Server-derived and never client-writable. Stored on the plot so the DB can
    enforce "one running number per series" with a partial unique index, and so
    generated codes are distinguishable from legacy ones without parsing any
    code string."""
    parts = "|".join(
        f"{len(component)}:{component}" for component in (supplier_code, month)
    )
    key = f"{_SERIES_KEY_SCHEME}|{parts}"
    if len(key) > MAX_PLOT_CODE_SERIES_KEY_LENGTH:
        # Only reachable via an absurdly long supplier code, which
        # normalize_supplier_code_for_plot_code does not itself bound. Surfaced
        # as the same clean error the rendered code would raise.
        raise PlotCodeTooLongError(
            f"Plot code series key would exceed {MAX_PLOT_CODE_SERIES_KEY_LENGTH} characters"
        )
    return key


def format_auto_plot_code(*, supplier_code: str, month: str, running: int) -> str:
    """Build "{supplierCode}-{YYMM}-{running}" with the running number
    zero-padded to a minimum of THREE digits.

    `supplier_code` is expected already normalized
    (normalize_supplier_code_for_plot_code) and is embedded IN FULL — never
    abbreviated or truncated, so the code always says which supplier it belongs
    to. Keyword-only so the three components can never be passed in the wrong
    order.

    Raises PlotCodeTooLongError if the result would not fit plot_code's column,
    rather than returning something the DB would reject or, worse, a truncated
    string that could collide with another plot's code."""
    code = f"{supplier_code}-{month}-{str(running).zfill(_RUNNING_MIN_WIDTH)}"
    if len(code) > MAX_PLOT_CODE_LENGTH:
        raise PlotCodeTooLongError(
            f"Generated plot code {len(code)} characters long exceeds the "
            f"{MAX_PLOT_CODE_LENGTH}-character limit"
        )
    return code


def preview_auto_plot_code(supplier_code: str | None, month: str | None) -> str:
    """Display-only rendering of the code a caller WILL generate, with "###"
    where the running number goes — it is allocated only at insert time, under
    the series, so no preview may invent one.

    A component that isn't known yet renders as a readable Thai placeholder
    rather than a fabricated value. Never raises: a preview must render for a
    half-filled form, including one whose supplier code would ultimately be
    refused. Never sent, never stored."""
    supplier = (supplier_code or "").strip().upper() or "<รหัส Supplier>"
    stamp = (month or "").strip() or "<ปีเดือน>"
    return f"{supplier}-{stamp}-###"
