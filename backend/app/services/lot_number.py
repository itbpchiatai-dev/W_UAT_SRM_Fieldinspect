"""Auto Lot number — the ONE place that normalizes PO / P.Code and formats a
server-generated lot number (round 8-5A; formula V2 round 8-12A). No endpoint /
import / repository duplicates this logic; they all call these helpers.

Auto Lot format V2 (round 8-12A):

    {cycleLabel}-{supplierCode}-{pCode}-{running}

  e.g. 2605-SUP010-WM-141-003
       26-may-SUP010-WM-141-004
       MAY26-SUP010-ABC-1000

Every component is used IN FULL after trimming — cycleLabel is never parsed as
a date, supplierCode is never abbreviated, and pCode is never clipped to three
characters. The running number is zero-padded to a MINIMUM width of 3 (1 ->
"001", 9 -> "009", 99 -> "099") and grows naturally beyond that (1000 ->
"1000") — str.zfill(3) never truncates and never wraps back to "001".

The PO number is NOT part of V2 (it was the leading component of V1). It
remains a first-class PlotCycle field; it simply no longer builds the lot.

V1 ({PO}-{plotCode}-{running}, 2-digit minimum) is GONE from the code, but the
rows it produced are untouched: their lot_no/lot_no_source/lot_running_no stay
exactly as generated, and they are identified by auto_lot_series_key IS NULL
(see migration 0048 and plot_cycle_repository).
"""
from __future__ import annotations

# lot_no is VARCHAR(100) (app/db/models/plot_cycle.py); a generated Auto Lot
# must fit that column.
MAX_LOT_NO_LENGTH = 100

# Minimum zero-pad width for the running number (3 digits: 001..999, then
# 1000+). Round 8-12A widened this from 2.
_RUNNING_MIN_WIDTH = 3

# auto_lot_series_key is VARCHAR(255) (migration 0048). The key is built from
# the same three components as the lot itself, so it is bounded by the same
# inputs; the guard exists so an over-long key surfaces as a clean validation
# error rather than a DataError at flush time.
MAX_AUTO_LOT_SERIES_KEY_LENGTH = 255

# Version stamp on the series-key encoding (round 8-12A.1). Bump ONLY together
# with a migration that rewrites stored keys — an unversioned change would let
# a new key silently coexist with an old one for the same logical series and
# restart its running sequence.
_SERIES_KEY_SCHEME = "v2"


class LotNumberTooLongError(ValueError):
    """A generated Auto Lot number would exceed MAX_LOT_NO_LENGTH. Raised by
    format_auto_lot_no so the caller can surface a clean 422 (never a 500,
    never a silently truncated lot number)."""


class AutoLotMissingComponentError(ValueError):
    """Round 8-12A — an Auto Lot was requested but one of its required
    components (cycleLabel / supplierCode / pCode) is blank.

    Replaces round 8-5B.1's AutoLotRequiresPoError: V2 does not use the PO, so
    "requires a PO" is no longer the truth to tell a user. Raised INSTEAD of
    silently clearing an existing lot to NULL — the caller surfaces a clean 422
    and the transaction rolls back, so the previous lot is preserved.

    `missing` names the blank component(s) so the API can say which field to
    fill in. It never carries a VALUE, only field names."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        joined = ", ".join(missing)
        super().__init__(
            f"Auto Lot requires {joined}; refusing to generate or clear the lot."
        )


def normalize_po_number(value: str | None) -> str | None:
    """Trim, drop-if-blank, and upper-case a PO number for NEW data. Idempotent
    (upper-casing an already-upper value is a no-op), so it's safe to apply at
    the schema boundary AND again in the repository. Returns None for None or a
    blank/whitespace-only string.

    Round 8-12A — PO is no longer part of the Auto Lot formula, but it is still
    a stored, normalized PlotCycle field; this function is unchanged."""
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed.upper()


def normalize_p_code(value: str | None) -> str | None:
    """Trim and drop-if-blank a product code. Case is PRESERVED (unlike PO) —
    a P.Code is stored as entered, and round 8-12A keeps that behaviour: the
    Auto Lot embeds the P.Code exactly as stored, in full ("WM-141" stays
    "WM-141", never "141" or "WM1"). Returns None for None or a blank string."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def normalize_cycle_label(value: str | None) -> str | None:
    """Trim and drop-if-blank a cycle label (round 8-12A).

    Deliberately NOT parsed, reformatted, case-folded or pattern-checked: the
    label is whatever the field team writes — "2605", "26-may", "MAY26",
    "รอบทดลอง" — and it goes into the Auto Lot verbatim. Adding a YYMM regex
    here would reject real, in-use labels.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def normalize_supplier_lot_no(value: str | None) -> str | None:
    """Trim and drop-if-blank the SUPPLIER's own lot number (round 8-12A).

    A free-form identifier the supplier assigns to the cycle. Stored alongside
    — never mixed into — the system's lot_no: it takes no part in the Auto Lot
    formula, the running number, or the Manual/Auto decision."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def build_auto_lot_series_key(
    supplier_code: str, cycle_label: str, p_code: str
) -> str:
    """The INTERNAL series identity a V2 Auto Lot running number counts within
    (round 8-12A): supplier + cycleLabel + pCode.

    Deliberately NOT the plot: V2's formula contains no plot code, so two plots
    of the same supplier sharing a (cycleLabel, pCode) series must draw from ONE
    running sequence or they would generate identical lot numbers.

    Server-derived and never client-writable. Stored on the cycle so the DB can
    enforce "one running number per series" with a partial unique index, and so
    V2 rows are distinguishable from V1 rows (which have NULL here) without
    re-parsing any lot string.

    Round 8-12A.1 — LENGTH-PREFIXED encoding, not a delimiter join. cycleLabel
    and pCode are free text: ANY separator character a user can type is a
    separator a user can forge, and round 8-12A's U+001F join was still
    forgeable by pasting a literal U+001F. Encoding each component as
    "<len>:<value>" makes the parse unambiguous regardless of content, so no
    pair of distinct (supplier, label, pCode) triples can ever produce one key:

        ("26", "may-1")  -> "v2|1:S|2:26|5:may-1"
        ("26-may", "1")  -> "v2|1:S|6:26-may|1:1"

    The "v2|" prefix version-stamps the scheme so a future change is
    distinguishable rather than silently colliding with old stored keys.
    Deterministic and stdlib-only — never Python's hash(), whose value is not
    stable across processes (PYTHONHASHSEED) and would break the DB index.
    """
    parts = "|".join(
        f"{len(component)}:{component}"
        for component in (supplier_code, cycle_label, p_code)
    )
    return f"{_SERIES_KEY_SCHEME}|{parts}"


def format_auto_lot_no(
    *,
    cycle_label: str,
    supplier_code: str,
    p_code: str,
    running: int,
) -> str:
    """Build "{cycleLabel}-{supplierCode}-{pCode}-{running}" (round 8-12A V2)
    with the running number zero-padded to a minimum of THREE digits.

    All three text components are expected already-normalized
    (normalize_cycle_label / normalize_p_code / the supplier's stored code) and
    are embedded IN FULL — nothing is abbreviated or truncated. Keyword-only so
    a caller can never silently pass V1's positional (plot_code, po_number,
    running) triple against the new meaning.

    Raises AutoLotMissingComponentError if any component is blank, and
    LotNumberTooLongError (never truncates) if the result exceeds
    MAX_LOT_NO_LENGTH. Never logs the lot or its components."""
    missing = _missing_components(
        cycle_label=cycle_label, supplier_code=supplier_code, p_code=p_code,
    )
    if missing:
        raise AutoLotMissingComponentError(missing)
    if running < 1:
        raise ValueError("Auto Lot running number must be >= 1")

    lot_no = (
        f"{cycle_label.strip()}-{supplier_code.strip()}-{p_code.strip()}"
        f"-{str(running).zfill(_RUNNING_MIN_WIDTH)}"
    )
    if len(lot_no) > MAX_LOT_NO_LENGTH:
        raise LotNumberTooLongError(
            f"Generated Auto Lot number is {len(lot_no)} characters, exceeding the "
            f"{MAX_LOT_NO_LENGTH}-character limit. Shorten the cycle label "
            f"(cycleLabel), the supplier code, or the product code (pCode)."
        )
    return lot_no


def _missing_components(
    *, cycle_label: str | None, supplier_code: str | None, p_code: str | None
) -> tuple[str, ...]:
    """Which required Auto Lot components are blank, in formula order. Returns
    FIELD NAMES only — never the submitted values."""
    missing: list[str] = []
    if not (cycle_label or "").strip():
        missing.append("cycleLabel")
    if not (supplier_code or "").strip():
        missing.append("supplierCode")
    if not (p_code or "").strip():
        missing.append("pCode")
    return tuple(missing)


def auto_lot_preview(cycle_label: str | None, supplier_code: str | None,
                     p_code: str | None) -> str:
    """A human-readable preview of the lot a row WOULD generate, with the
    running number shown as "###" because it is only decided server-side at
    commit (round 8-12A; replaces V1's "{PO}-{plotCode}-XX").

    Never raises and never allocates a running number — this is display-only,
    used by the Excel import's read-only preview. Missing components render as
    their own placeholder so the user can see exactly which part is absent."""
    label = (cycle_label or "").strip() or "<cycleLabel>"
    supplier = (supplier_code or "").strip() or "<supplierCode>"
    code = (p_code or "").strip() or "<pCode>"
    return f"{label}-{supplier}-{code}-###"
