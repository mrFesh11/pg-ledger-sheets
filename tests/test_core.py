from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from orders import ORDERS, PROFILE, TOTAL

from pg_ledger_sheets.core import Record, State, plan
from pg_ledger_sheets.sinks import XlsxSink

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
D9, D10, D11 = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)


def order(id: int, at: datetime = NOW, amount: int = 10000, status: str = "settled") -> Record:
    return Record(id, at, status != "pending", {"status": status, "customer": "acme", "amount_cents": amount})


def revenue(records: list[Record]):
    return lambda day: sum(PROFILE.revenue(r) for r in records if r.done and r.at.date() == day)


def test_rows_in_id_order_and_skipped_records():
    batch = plan(PROFILE, State(), [order(2), order(1), order(3, status="failed")], NOW, lambda day: 0)
    assert [row[5] for row in batch.rows[ORDERS]] == [1, 2]
    assert batch.state.last_id == 3


def test_day_total_closes_previous_days_only():
    records = [order(1, NOW - timedelta(days=2)), order(2, NOW - timedelta(days=1), 5000), order(3)]
    rows = plan(PROFILE, State(), records, NOW, revenue(records)).rows[ORDERS]
    assert [row[2] for row in rows] == ["settled", TOTAL, "settled", TOTAL, "settled"]
    assert [rows[1][0], rows[1][4], rows[3][4]] == [D9, 100.0, 50.0]


def test_day_total_from_sheet_days_with_grace():
    state = State(last_id=4, first_month=date(2026, 11, 1), last_month=date(2026, 11, 1))
    days = frozenset({D10, D11})
    done = frozenset({D10})
    assert plan(PROFILE, state, [], NOW, lambda day: 700, sheet_days=days, total_days=done).rows[ORDERS] == []
    just_after_midnight = datetime(2026, 11, 12, 0, 5, tzinfo=UTC)
    assert plan(PROFILE, state, [], just_after_midnight, lambda day: 700, sheet_days=days, total_days=done).rows[ORDERS] == []
    next_day = plan(PROFILE, state, [], NOW + timedelta(days=1), lambda day: 700, sheet_days=days, total_days=done).rows[ORDERS]
    assert [(row[2], row[0], row[4]) for row in next_day] == [(TOTAL, D11, 7.0)]
    assert plan(PROFILE, state, [], NOW + timedelta(days=1), lambda day: 700, sheet_days=days, total_days=days).rows[ORDERS] == []


def test_rerun_after_crash_adds_missing_totals_once():
    records = [order(1, NOW - timedelta(days=2)), order(2)]
    batch = plan(PROFILE, State(), records, NOW, revenue(records), {ORDERS: frozenset({1, 2})}, frozenset({D9, D11}))
    assert [(row[2], row[0]) for row in batch.rows[ORDERS]] == [(TOTAL, D9)]


def test_already_exported_rows_are_skipped():
    batch = plan(PROFILE, State(), [order(1), order(2)], NOW, lambda day: 0, {ORDERS: frozenset({1})})
    assert [row[5] for row in batch.rows[ORDERS]] == [2]


def test_pending_is_kept_then_exported_and_expires():
    first = plan(PROFILE, State(), [order(1, status="pending"), order(2)], NOW, lambda day: 0)
    assert (first.state.pending, first.state.last_id) == ([1], 2)
    assert [row[5] for row in first.rows[ORDERS]] == [2]
    second = plan(PROFILE, first.state, [order(1)], NOW, lambda day: 0)
    assert [row[5] for row in second.rows[ORDERS]] == [1]
    assert second.state.pending == []
    stale = plan(PROFILE, State(), [order(1, NOW - timedelta(days=8), status="pending")], NOW, lambda day: 0)
    assert stale.state.pending == []


def test_months():
    first = plan(PROFILE, State(), [order(1, datetime(2026, 9, 15, tzinfo=UTC))], NOW, lambda day: 0)
    assert first.months == [(3, date(2026, 9, 1)), (4, date(2026, 10, 1)), (5, date(2026, 11, 1))]
    assert plan(PROFILE, first.state, [], NOW, lambda day: 0).months == []
    assert plan(PROFILE, first.state, [], NOW + timedelta(days=30), lambda day: 0).months == [(6, date(2026, 12, 1))]


def test_since_cuts_old_history():
    batch = plan(PROFILE, State(), [order(1, NOW - timedelta(days=60)), order(2)], NOW, lambda day: 0, since=date(2026, 9, 17))
    assert [row[5] for row in batch.rows[ORDERS]] == [2]
    assert batch.state.first_month == date(2026, 11, 1)


def test_state_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = State(last_id=9, pending=[3], first_month=date(2026, 9, 1), last_month=date(2026, 11, 1))
    state.save(path)
    assert State.load(path) == state
    assert State.load(tmp_path / "none.json") == State()


def test_xlsx_export(tmp_path: Path):
    from openpyxl import load_workbook

    records = [order(1, NOW - timedelta(days=1)), order(2)]
    batch = plan(PROFILE, State(), records, NOW, revenue(records))
    sink = XlsxSink(tmp_path / "book.xlsx", PROFILE)
    for title, rows in batch.rows.items():
        sink.append(title, rows)
    sink.set_months(batch.months)
    sink.save()
    book = load_workbook(tmp_path / "book.xlsx")
    assert book.sheetnames == [ORDERS, "Summary"]
    assert [row[2] for row in book[ORDERS].iter_rows(min_row=2, values_only=True)] == ["settled", TOTAL, "settled"]
    assert book["Summary"]["A3"].value.date() == date(2026, 11, 1)


def test_xlsx_keeps_database_text_as_text(tmp_path: Path):
    from openpyxl import load_workbook

    sink = XlsxSink(tmp_path / "book.xlsx", PROFILE)
    sink.append(ORDERS, [[D11, "", "settled", "=HYPERLINK(1)", 1.0, 1]])
    sink.save()
    cell = load_workbook(tmp_path / "book.xlsx")[ORDERS]["D2"]
    assert (cell.value, cell.data_type) == ("=HYPERLINK(1)", "s")
