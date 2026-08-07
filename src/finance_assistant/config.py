"""Project-wide constants and declarative dataset schemas.

Schema shape (required columns, date/numeric/string columns) lives here as
data, not behavior. `data/validation.py` consumes these schemas; it owns the
coercion/validation logic itself.
"""

from pathlib import Path
from typing import TypedDict

# <repo_root>/data, independent of the caller's cwd.
DATA_DIR: Path = Path(__file__).resolve().parents[2] / "data"

# accrual_date is the financial date basis by default (never posting_date).
DEFAULT_FINANCIAL_DATE_FIELD = "accrual_date"

GL_TRANSACTIONS_FILE = "gl_transactions.csv"
CHART_OF_ACCOUNTS_FILE = "chart_of_accounts.csv"
BUDGET_FILE = "budget.csv"
FX_RATES_FILE = "fx_rates.csv"
VENDORS_FILE = "vendors.csv"


class DatasetSchema(TypedDict):
    filename: str
    required_columns: list[str]
    date_columns: list[str]
    numeric_columns: list[str]
    string_columns: list[str]


GL_SCHEMA: DatasetSchema = {
    "filename": GL_TRANSACTIONS_FILE,
    "required_columns": [
        "txn_id",
        "posting_date",
        "accrual_date",
        "entity",
        "cost_centre",
        "account_code",
        "amount",
        "currency",
        "vendor_id",
        "doc_ref",
        "approval_ref",
        "memo",
    ],
    "date_columns": ["posting_date", "accrual_date"],
    "numeric_columns": ["amount"],
    "string_columns": [
        "txn_id",
        "entity",
        "cost_centre",
        "account_code",
        "currency",
        "vendor_id",
        "doc_ref",
        "approval_ref",
        "memo",
    ],
}

COA_SCHEMA: DatasetSchema = {
    "filename": CHART_OF_ACCOUNTS_FILE,
    "required_columns": [
        "account_code",
        "account_name",
        "parent_code",
        "parent_name",
        "statement_line",
        "valid_from",
        "valid_to",
    ],
    "date_columns": ["valid_from", "valid_to"],
    "numeric_columns": [],
    "string_columns": [
        "account_code",
        "account_name",
        "parent_code",
        "parent_name",
        "statement_line",
    ],
}

# period_month (budget, fx) is a YYYY-MM period key, kept as string rather than
# coerced to a date: it is not a point-in-time date and must never be compared
# directly against accrual_date/posting_date.
BUDGET_SCHEMA: DatasetSchema = {
    "filename": BUDGET_FILE,
    "required_columns": [
        "entity",
        "cost_centre",
        "account_code",
        "period_month",
        "budget_amount",
        "currency",
    ],
    "date_columns": [],
    "numeric_columns": ["budget_amount"],
    "string_columns": ["entity", "cost_centre", "account_code", "period_month", "currency"],
}

FX_SCHEMA: DatasetSchema = {
    "filename": FX_RATES_FILE,
    "required_columns": ["period_month", "currency", "rate_to_usd"],
    "date_columns": [],
    "numeric_columns": ["rate_to_usd"],
    "string_columns": ["period_month", "currency"],
}

VENDORS_SCHEMA: DatasetSchema = {
    "filename": VENDORS_FILE,
    "required_columns": ["vendor_id", "vendor_name", "category", "country"],
    "date_columns": [],
    "numeric_columns": [],
    "string_columns": ["vendor_id", "vendor_name", "category", "country"],
}
