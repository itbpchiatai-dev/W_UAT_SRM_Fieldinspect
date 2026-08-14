"""Cycle-level EXTERNAL reference fields (round 8-21A) — Oracle Supplier Code,
Oracle Invoice, and Ref Account. Three independent, optional, free-text
identifiers a back-office (Oracle-integrated) process may want attached to a
specific planting cycle. They carry no business logic of their own: unlike
lot_no/po_number/p_code (services/lot_number.py) they never feed the Auto Lot
formula, a running number, or any Manual/Auto decision, and unlike
cycle_label they are never required. Kept in their own module rather than
folded into lot_number.py (whose docstring scopes it to "Auto Lot number")
since none of these three are lot-related in any way — only their storage
grain (one per PlotCycle) and their normalization RULE happen to match.

All three share the exact same normalization: trim, and blank/whitespace-only
becomes None. No case change, no reformatting — the value is stored exactly
as the user typed it (minus surrounding whitespace)."""
from __future__ import annotations


def normalize_cycle_reference_text(value: str | None) -> str | None:
    """Trim and drop-if-blank one of oracle_supplier_code / oracle_invoice /
    ref_account. Returns None for None or a blank/whitespace-only string —
    the single shared rule for all three fields, reused everywhere they are
    read from a request or an Excel cell (schemas/plot.py,
    plot_cycle_repository.py, services/plot_import.py) so the three call
    sites can never drift on what "blank" means."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
