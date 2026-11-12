from datetime import date
from zoneinfo import ZoneInfo

from pg_ledger_sheets.core import SUMMARY_FIRST_MONTH_ROW, Cell, Profile, Record, Sheet

UTC = ZoneInfo("UTC")
ORDERS = "Payments"
TOTAL = "Day total"

SOURCE = """
    select p.id, coalesce(p.settled_at, p.created_at) as at, p.status <> 'pending' as done, p.status, p.customer, p.amount_cents
    from payments p
"""


def cents(record: Record) -> int:
    amount: int = record.data["amount_cents"]
    return amount


def route(record: Record) -> tuple[str, list[Cell]] | None:
    if record.data["status"] == "failed":
        return None
    local = record.at.astimezone(UTC)
    return ORDERS, [
        local.date(),
        local.time().replace(microsecond=0, tzinfo=None),
        record.data["status"],
        record.data["customer"],
        cents(record) / 100,
        record.id,
    ]


def total_row(day: date, total: int) -> list[Cell]:
    return [day, "", TOTAL, "", total / 100, ""]


def month_row(month: date, row: int, sep: str) -> list[Cell]:
    period = sep.join([f"'{ORDERS}'!A:A", f'">="&A{row}', f"'{ORDERS}'!A:A", f'"<"&EDATE(A{row}{sep}1)'])
    return [month, f"=SUMIFS('{ORDERS}'!E:E{sep}{period}{sep}'{ORDERS}'!C:C{sep}\"<>{TOTAL}\")", f"=COUNTIFS({period}{sep}'{ORDERS}'!C:C{sep}\"settled\")"]


def summary_total(sep: str) -> list[Cell]:
    return ["TOTAL", *(f"=SUM({column}{SUMMARY_FIRST_MONTH_ROW}:{column}1000)" for column in "BC")]


PROFILE = Profile(
    source=SOURCE,
    timezone=UTC,
    locale="en_US",
    separator=",",
    sheets=[Sheet(ORDERS, ["Date", "Time", "Status", "Customer", "Amount", "Payment ID"], {"A": "yyyy-mm-dd", "B": "hh:mm", "E": "#,##0.00"})],
    ledger=ORDERS,
    kind_column=2,
    total_label=TOTAL,
    summary=Sheet("Summary", ["Month", "Revenue", "Payments"], {"A": "yyyy-mm", "B": "#,##0.00", "C": "0"}),
    extra=[],
    route=route,
    revenue=cents,
    total_row=total_row,
    summary_total=summary_total,
    month_row=month_row,
)
