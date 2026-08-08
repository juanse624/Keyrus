"""Loads the real data/*.csv datasets once and derives the runtime
invocation parameters the deterministic eval cases need (years, date
ranges) directly from whatever is actually loaded.

This is the mechanism that keeps evals/questions.yaml free of any
dataset-specific literal (a year, a date range): the challenge's grading
dataset shares gl_transactions.csv/chart_of_accounts.csv/budget.csv/
fx_rates.csv/vendors.csv's columns but not their numbers, so "the most
recent year" or "the year budget.csv covers" must be read off the loaded
frame, never hardcoded.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from finance_assistant import config
from finance_assistant.config import DEFAULT_FINANCIAL_DATE_FIELD
from finance_assistant.data.loaders import (
    load_budget,
    load_chart_of_accounts,
    load_fx_rates,
    load_gl_transactions,
    load_vendors,
)


@dataclass(frozen=True)
class Dataset:
    gl: pd.DataFrame
    coa: pd.DataFrame
    fx: pd.DataFrame
    budget: pd.DataFrame
    vendors: pd.DataFrame
    documents_dir: Path = field(default_factory=lambda: config.DATA_DIR / "documents")

    def two_most_recent_years(self, date_field: str = DEFAULT_FINANCIAL_DATE_FIELD) -> tuple[int, int]:
        """(current, prior) = the two most recent distinct years present in
        gl[date_field], descending. Used by Q2 (travel_comparison) instead
        of a literal year pair."""
        years = sorted({int(y) for y in self.gl[date_field].dropna().dt.year.unique()}, reverse=True)
        if len(years) < 2:
            raise ValueError(f"need at least 2 distinct years in gl['{date_field}'] to compare, found {years}")
        return years[0], years[1]

    def budget_year(self) -> int:
        """The single year budget.csv's period_month values cover (R5:
        budget covers exactly one year). Used by Q5 (budget_variance)
        instead of a literal year."""
        years = sorted({str(pm)[:4] for pm in self.budget["period_month"].dropna().unique()})
        if not years:
            raise ValueError("budget.csv has no period_month values")
        if len(years) > 1:
            # R5 assumes one year; if a conforming dataset violates that,
            # surface it loudly rather than silently picking one.
            raise ValueError(f"budget.csv covers more than one year: {years}")
        return int(years[0])

    def full_date_range(self, date_field: str = DEFAULT_FINANCIAL_DATE_FIELD) -> tuple[pd.Timestamp, pd.Timestamp]:
        """(start, end) = the min/max of gl[date_field]. Used by Q4/Q6/Q8,
        whose literal question wording doesn't name a period."""
        series = self.gl[date_field].dropna()
        if series.empty:
            raise ValueError(f"gl['{date_field}'] has no non-null values")
        return series.min(), series.max()


def load_dataset() -> Dataset:
    return Dataset(
        gl=load_gl_transactions(),
        coa=load_chart_of_accounts(),
        fx=load_fx_rates(),
        budget=load_budget(),
        vendors=load_vendors(),
    )
