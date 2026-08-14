"""Dashboard summary schema."""
from __future__ import annotations

from app.schemas.base import CamelBaseModel


class CropTypeStat(CamelBaseModel):
    crop_type: str | None = None
    count: int


class DashboardSummary(CamelBaseModel):
    total_records: int
    records_this_month: int
    avg_condition_score: float | None = None  # avg of the 4 condition scores (1-10)
    low_score_count: int  # records with any condition score <= 3 (needs attention)
    total_plots: int
    total_suppliers: int | None = None  # None when caller lacks suppliers.read
    by_crop_type: list[CropTypeStat]
