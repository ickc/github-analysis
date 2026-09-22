"""GitHub's billing usage report, as an optional companion to the dataset.

The :class:`~github_analysis.dataset.ActionsDataset` *estimates* billed minutes
from job timestamps. When the account's billing usage report has been cached
(see :func:`github_analysis.fetch.fetch_billing_usage`), :class:`BillingUsage`
provides the minutes GitHub actually billed, per day, repository and SKU.

The report is optional. :meth:`BillingUsage.from_cache` returns ``None`` when
nothing was cached (typically because the token lacks the organisation owner or
billing manager role), and every consumer must treat that as "use the
estimates only".

The report does not record repository visibility, so the private-only view is
derived from the dataset's cached repository metadata, as for the estimates.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DateRange
from .domain import BillingUsageItem, Visibility
from .fetch import CachePaths, billing_months

__all__ = ["BillingUsage", "BILLING_COLUMNS"]

BILLING_COLUMNS: tuple[str, ...] = (
    "date",
    "month",
    "repo",
    "visibility",
    "sku",
    "runtime_os",
    "minutes",
    "gross_amount",
    "discount_amount",
    "net_amount",
)


def _item_to_row(item: BillingUsageItem, visibility: Visibility) -> dict[str, Any]:
    return {
        "date": item.date,
        "month": item.month,
        "repo": item.repo,
        "visibility": visibility.value,
        "sku": item.sku,
        "runtime_os": item.runtime_os.value,
        "minutes": item.quantity,
        "gross_amount": item.gross_amount,
        "discount_amount": item.discount_amount,
        "net_amount": item.net_amount,
    }


def _in_range(item: BillingUsageItem, date_range: DateRange) -> bool:
    if item.date is None:
        return False
    date = item.date if item.date.tzinfo else item.date.replace(tzinfo=timezone.utc)
    since, until = (
        d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        for d in (date_range.since, date_range.until)
    )
    return since <= date < until


@dataclass(frozen=True)
class BillingUsage:
    """Cached billing usage items for one account, plus the months they cover."""

    org: str
    items: tuple[BillingUsageItem, ...]
    months: tuple[str, ...]
    expected_months: tuple[str, ...] = ()

    @classmethod
    def from_cache(
        cls, org: str, cache_dir: Path, date_range: DateRange | None = None
    ) -> "BillingUsage | None":
        """Load the cached report, or ``None`` if no month was cached.

        With ``date_range``, only the months and items inside it are kept and
        :attr:`expected_months` lists every month the range covers.
        """
        paths = CachePaths(cache_dir, org)
        expected = tuple(billing_months(date_range)) if date_range else ()
        files = sorted(paths.billing_dir.glob("*.json")) if paths.billing_dir.exists() else []
        if expected:
            files = [f for f in files if f.stem in expected]
        loaded = dict(_load_months(files))
        if not loaded:
            return None
        items = tuple(item for month_items in loaded.values() for item in month_items)
        if date_range is not None:
            items = tuple(item for item in items if _in_range(item, date_range))
        return cls(org=org, items=items, months=tuple(sorted(loaded)), expected_months=expected)

    @property
    def missing_months(self) -> tuple[str, ...]:
        """Months in the requested range with no cached report."""
        return tuple(m for m in self.expected_months if m not in self.months)

    def actions_minutes_frame(self, visibility: Mapping[str, Visibility]) -> pd.DataFrame:
        """One row per Actions-minutes usage item, tagged with repo visibility."""
        rows = [
            _item_to_row(item, visibility.get(item.repo, Visibility.UNKNOWN))
            for item in self.items
            if item.is_actions_minutes
        ]
        if not rows:
            return pd.DataFrame(columns=BILLING_COLUMNS)
        return pd.DataFrame.from_records(rows, columns=BILLING_COLUMNS)


def _load_months(files: Iterable[Path]) -> Iterable[tuple[str, list[BillingUsageItem]]]:
    for path in files:
        try:
            payloads = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payloads, list):
            continue
        items = [BillingUsageItem.from_payload(p) for p in payloads if isinstance(p, dict)]
        yield path.stem, [item for item in items if item is not None]
