from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from .core import Record

OVERLAP_IDS = 1000


def _record(row: dict[str, Any]) -> Record:
    at = row.pop("at")
    if not isinstance(at, datetime):
        raise TypeError(f"запись {row['id']}: столбец at должен быть timestamp и не NULL")
    return Record(row.pop("id"), at if at.tzinfo else at.replace(tzinfo=UTC), row.pop("done"), row)


class Database:
    def __init__(self, dsn: str, source: str) -> None:
        options = "-c default_transaction_read_only=on -c timezone=UTC -c statement_timeout=60000"
        self.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10, options=options)
        self.source = source

    def _select(self, where: str, params: tuple[object, ...]) -> list[Record]:
        query = f"select * from ({self.source}) s where {where} order by s.id"
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query.encode(), params)
            return [_record(row) for row in cur.fetchall()]

    def fetch_new(self, last_id: int, pending: list[int]) -> list[Record]:
        return self._select("s.id > %s or s.id = any(%s) or s.at > now() - interval '2 days'", (last_id - OVERLAP_IDS, pending))

    def day_records(self, day: date, timezone: ZoneInfo) -> list[Record]:
        start = datetime.combine(day, time(), timezone)
        return self._select("s.done and s.at >= %s and s.at < %s", (start, start + timedelta(days=1)))
