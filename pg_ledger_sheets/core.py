import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

type Cell = str | int | float | date | time

PENDING_TTL = timedelta(days=7)
DAY_GRACE = timedelta(minutes=15)
SUMMARY_FIRST_MONTH_ROW = 3


@dataclass(frozen=True)
class Record:
    id: int
    at: datetime
    done: bool
    data: Mapping[str, Any]


@dataclass(frozen=True)
class Sheet:
    title: str
    header: Sequence[str] = ()
    formats: Mapping[str, str] = field(default_factory=dict[str, str])
    formula: Callable[[str], str] | None = None


@dataclass(frozen=True)
class Profile:
    source: str
    timezone: ZoneInfo
    locale: str
    separator: str
    sheets: Sequence[Sheet]
    ledger: str
    kind_column: int
    total_label: str
    summary: Sheet
    extra: Sequence[Sheet]
    route: Callable[[Record], tuple[str, list[Cell]] | None]
    revenue: Callable[[Record], int]
    total_row: Callable[[date, int], list[Cell]]
    summary_total: Callable[[str], list[Cell]]
    month_row: Callable[[date, int, str], list[Cell]]

    def __post_init__(self) -> None:
        titles = [sheet.title for sheet in self.sheets]
        if self.ledger not in titles:
            raise ValueError(f"ledger {self.ledger!r} не входит в sheets")
        if any(not sheet.header or sheet.formula for sheet in (*self.sheets, self.summary)):
            raise ValueError("у листов с записями и листа итогов должен быть заголовок и не должно быть формулы")
        width = len(self.sheets[titles.index(self.ledger)].header)
        total = self.total_row(date(2000, 1, 1), 0)
        if len(total) != width or not 0 <= self.kind_column < width or total[self.kind_column] != self.total_label:
            raise ValueError("total_row должен совпадать по ширине с заголовком и содержать total_label в столбце kind_column")
        summary = len(self.summary.header)
        if len(self.summary_total(self.separator)) != summary or len(self.month_row(date(2000, 1, 1), SUMMARY_FIRST_MONTH_ROW, self.separator)) != summary:
            raise ValueError("summary_total и month_row должны совпадать по ширине с заголовком листа итогов")

    @property
    def layout(self) -> Sequence[Sheet]:
        manual = [sheet for sheet in self.extra if sheet.formula is None]
        return (*self.sheets, *manual, self.summary, *(sheet for sheet in self.extra if sheet.formula))


@dataclass
class State:
    last_id: int = 0
    pending: list[int] = field(default_factory=list[int])
    first_month: date | None = None
    last_month: date | None = None

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            last_id=raw["last_id"],
            pending=raw["pending"],
            first_month=_date(raw["first_month"]),
            last_month=_date(raw["last_month"]),
        )

    def save(self, path: Path) -> None:
        raw = {
            "last_id": self.last_id,
            "pending": self.pending,
            "first_month": _iso(self.first_month),
            "last_month": _iso(self.last_month),
        }
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as file:
            file.write(json.dumps(raw))
            file.flush()
            os.fsync(file.fileno())
        tmp.replace(path)


@dataclass
class Batch:
    rows: dict[str, list[list[Cell]]]
    months: list[tuple[int, date]]
    state: State


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def _month_index(first: date, month: date) -> int:
    return (month.year - first.year) * 12 + month.month - first.month


def plan(
    profile: Profile,
    state: State,
    records: Sequence[Record],
    now: datetime,
    day_revenue: Callable[[date], int],
    exported: Mapping[str, frozenset[int]] | None = None,
    sheet_days: frozenset[date] = frozenset(),
    total_days: frozenset[date] = frozenset(),
    since: date = date.min,
) -> Batch:
    exported = exported or {}
    today = now.astimezone(profile.timezone).date()
    rows: dict[str, list[list[Cell]]] = {sheet.title: [] for sheet in profile.sheets}
    widths = {sheet.title: len(sheet.header) for sheet in profile.sheets}
    ledger: list[tuple[date, list[Cell]]] = []
    pending: list[int] = []
    earliest = today

    for record in sorted(records, key=lambda r: r.id):
        if not record.done:
            if now - record.at < PENDING_TTL:
                pending.append(record.id)
            continue
        day = record.at.astimezone(profile.timezone).date()
        if day < since:
            continue
        routed = profile.route(record)
        if routed is None:
            continue
        earliest = min(earliest, day)
        title, row = routed
        if len(row) != widths.get(title) or row[0] != day or row[-1] != record.id:
            raise ValueError(f"запись {record.id}: строка для листа {title!r} должна совпадать с заголовком, начинаться датой и заканчиваться id")
        if record.id in exported.get(title, frozenset()):
            continue
        if title == profile.ledger:
            ledger.append((day, row))
        else:
            rows[title].append(row)

    days = (sheet_days | {day for day, _ in ledger}) - total_days
    closed = sorted(day for day in days if now >= datetime.combine(day + timedelta(days=1), time(), profile.timezone) + DAY_GRACE)
    for day, row in ledger:
        while closed and closed[0] < day:
            rows[profile.ledger].append(profile.total_row(closed[0], day_revenue(closed.pop(0))))
        rows[profile.ledger].append(row)
    rows[profile.ledger].extend(profile.total_row(day, day_revenue(day)) for day in closed)

    first_month = state.first_month or earliest.replace(day=1)
    month = _next_month(state.last_month) if state.last_month else first_month
    current = today.replace(day=1)
    months: list[tuple[int, date]] = []
    while month <= current:
        months.append((SUMMARY_FIRST_MONTH_ROW + _month_index(first_month, month), month))
        month = _next_month(month)

    return Batch(
        rows=rows,
        months=months,
        state=State(
            last_id=max([state.last_id, *(record.id for record in records)]),
            pending=pending,
            first_month=first_month,
            last_month=max(current, state.last_month or current),
        ),
    )
