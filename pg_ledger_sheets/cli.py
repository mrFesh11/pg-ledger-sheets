import argparse
import fcntl
import os
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path

from .core import Profile, State, plan
from .db import Database
from .sinks import GoogleSink, Sink, XlsxSink

DEFAULT_PROFILE = "pg_ledger_sheets.profiles.bedolaga"


def load_profile() -> Profile:
    profile = import_module(os.environ.get("LEDGER_PROFILE", DEFAULT_PROFILE)).PROFILE
    if not isinstance(profile, Profile):
        raise TypeError("PROFILE должен быть экземпляром pg_ledger_sheets.core.Profile")
    return profile


def run(profile: Profile, db: Database, sink: Sink, state: State) -> State:
    batch = plan(
        profile,
        state,
        db.fetch_new(state.last_id, state.pending),
        datetime.now(UTC),
        lambda day: sum(profile.revenue(record) for record in db.day_records(day, profile.timezone)),
        {sheet.title: sink.exported(sheet) for sheet in profile.sheets},
        *sink.days(),
        date.fromisoformat(os.environ.get("LEDGER_SINCE", "0001-01-01")),
    )
    for title, rows in batch.rows.items():
        sink.append(title, rows)
    sink.set_months(batch.months)
    added = ", ".join(f"{title}: +{len(rows)}" for title, rows in batch.rows.items() if rows) or "новых строк нет"
    print(f"{added}; месяцы: +{len(batch.months)}; last_id={batch.state.last_id}")
    return batch.state


def main() -> None:
    parser = argparse.ArgumentParser(prog="pg-ledger-sheets")
    parser.add_argument("command", choices=["init", "sync", "xlsx"])
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    profile = load_profile()

    if args.command == "xlsx":
        if args.path is None:
            parser.error("xlsx: нужен путь к файлу")
        book = XlsxSink(args.path, profile)
        run(profile, Database(os.environ["LEDGER_DSN"], profile.source), book, State())
        book.save()
        return

    sink = GoogleSink(os.environ["LEDGER_KEY"], os.environ["LEDGER_SHEET_ID"], profile)
    if args.command == "init":
        sink.init()
        return
    state_path = Path(os.environ["LEDGER_STATE"])
    lock = state_path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run(profile, Database(os.environ["LEDGER_DSN"], profile.source), sink, State.load(state_path)).save(state_path)


if __name__ == "__main__":
    main()
