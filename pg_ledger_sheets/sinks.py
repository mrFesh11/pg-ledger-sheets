from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol, cast

import gspread
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1

from .core import Cell, Profile, Sheet

EPOCH = date(1899, 12, 30)
CHUNK = 5000


class Sink(Protocol):
    def exported(self, sheet: Sheet) -> frozenset[int]: ...
    def days(self) -> tuple[frozenset[date], frozenset[date]]: ...
    def append(self, title: str, rows: Sequence[Sequence[Cell]]) -> None: ...
    def set_months(self, months: Sequence[tuple[int, date]]) -> None: ...


def _serial(cell: Cell) -> str | int | float:
    if isinstance(cell, datetime):
        raise TypeError("datetime в ячейке")
    if isinstance(cell, date):
        return (cell - EPOCH).days
    if isinstance(cell, time):
        return (cell.hour * 3600 + cell.minute * 60 + cell.second) / 86400
    return cell


def _kind(pattern: str) -> str:
    if "y" in pattern or "d" in pattern:
        return "DATE"
    return "TIME" if "h" in pattern else "NUMBER"


def _letter(column: int) -> str:
    return rowcol_to_a1(1, column)[:-1]


class GoogleSink:
    def __init__(self, key_path: str, sheet_id: str, profile: Profile) -> None:
        client = gspread.service_account(filename=key_path)
        client.set_timeout(60)
        self.book = client.open_by_key(sheet_id)
        self.profile = profile

    def init(self) -> None:
        profile = self.profile
        properties = {"locale": profile.locale, "timeZone": profile.timezone.key}
        self.book.batch_update({"requests": [{"updateSpreadsheetProperties": {"properties": properties, "fields": "locale,timeZone"}}]})
        for spec in profile.layout:
            try:
                sheet = self.book.worksheet(spec.title)
                created = False
            except gspread.WorksheetNotFound:
                sheet = self.book.add_worksheet(spec.title, rows=1000, cols=max(len(spec.header), 26 if spec.formula else 1))
                created = True
            if spec.formula:
                sheet.update(values=[[spec.formula(profile.separator)]], range_name="A1", value_input_option=ValueInputOption.user_entered)
            else:
                sheet.update(values=[list(spec.header)], range_name="A1")
            sheet.freeze(rows=1)
            sheet.format("1:1", {"textFormat": {"bold": True}})
            for column, pattern in spec.formats.items():
                sheet.format(f"{column}2:{column}", {"numberFormat": {"type": _kind(pattern), "pattern": pattern}})
            if spec is profile.summary:
                sheet.update(
                    values=[[_serial(cell) for cell in profile.summary_total(profile.separator)]],
                    range_name=f"A2:{_letter(len(spec.header))}2",
                    value_input_option=ValueInputOption.user_entered,
                )
                sheet.format("2:2", {"textFormat": {"bold": True}})
            if created and spec.title == profile.ledger:
                formula = f'=${_letter(profile.kind_column + 1)}2="{profile.total_label}"'
                style = {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}}
                rule = {
                    "ranges": [{"sheetId": sheet.id, "startRowIndex": 1}],
                    "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": style},
                }
                self.book.batch_update({"requests": [{"addConditionalFormatRule": {"index": 0, "rule": rule}}]})

    def exported(self, sheet: Sheet) -> frozenset[int]:
        column = _letter(len(sheet.header))
        raw = self.book.worksheet(sheet.title).get(f"{column}2:{column}", value_render_option=ValueRenderOption.unformatted)
        cells = (row[0] for row in cast(Sequence[Sequence[object]], raw) if row)
        return frozenset(int(cell) for cell in cells if isinstance(cell, int | float) or (isinstance(cell, str) and cell.isdigit()))

    def days(self) -> tuple[frozenset[date], frozenset[date]]:
        column = self.profile.kind_column
        raw = self.book.worksheet(self.profile.ledger).get(f"A2:{_letter(column + 1)}", value_render_option=ValueRenderOption.unformatted)
        dated: list[tuple[date, bool]] = []
        for row in cast(Sequence[Sequence[object]], raw):
            if row and isinstance(row[0], int | float):
                dated.append((EPOCH + timedelta(days=int(row[0])), len(row) > column and row[column] == self.profile.total_label))
        return frozenset(day for day, _ in dated), frozenset(day for day, total in dated if total)

    def append(self, title: str, rows: Sequence[Sequence[Cell]]) -> None:
        sheet = self.book.worksheet(title)
        for start in range(0, len(rows), CHUNK):
            sheet.append_rows(
                [[_serial(cell) for cell in row] for row in rows[start : start + CHUNK]],
                value_input_option=ValueInputOption.raw,
                table_range="A1",
            )

    def set_months(self, months: Sequence[tuple[int, date]]) -> None:
        summary = self.profile.summary
        sheet = self.book.worksheet(summary.title)
        for row, month in months:
            sheet.update(
                values=[[_serial(cell) for cell in self.profile.month_row(month, row, self.profile.separator)]],
                range_name=f"A{row}:{_letter(len(summary.header))}{row}",
                value_input_option=ValueInputOption.user_entered,
            )


class XlsxSink:
    def __init__(self, path: Path, profile: Profile) -> None:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        self.path = path
        self.profile = profile
        self.bold = Font(bold=True)
        self.book = Workbook()
        self.book.remove(self.book.worksheets[0])
        for spec in profile.layout:
            sheet = self.book.create_sheet(spec.title)
            sheet.append(list(spec.header) if spec.formula is None else ["формула работает только в Google Sheets"])
            sheet.freeze_panes = "A2"
            if spec is profile.summary:
                sheet.append(profile.summary_total(","))
            for row in sheet.iter_rows():
                for cell in row:
                    cell.font = self.bold

    def exported(self, sheet: Sheet) -> frozenset[int]:
        return frozenset()

    def days(self) -> tuple[frozenset[date], frozenset[date]]:
        return frozenset(), frozenset()

    def append(self, title: str, rows: Sequence[Sequence[Cell]]) -> None:
        sheet = self.book[title]
        for row in rows:
            sheet.append(list(row))
            for cell in sheet[sheet.max_row]:
                if cell.data_type == "f":
                    cell.data_type = "s"
            if title == self.profile.ledger and row[self.profile.kind_column] == self.profile.total_label:
                for cell in sheet[sheet.max_row]:
                    cell.font = self.bold

    def set_months(self, months: Sequence[tuple[int, date]]) -> None:
        sheet = self.book[self.profile.summary.title]
        for row, month in months:
            for column, cell in enumerate(self.profile.month_row(month, row, ","), start=1):
                sheet.cell(row=row, column=column, value=cell)

    def save(self) -> None:
        for spec in self.profile.layout:
            if spec.formula:
                continue
            sheet = self.book[spec.title]
            for column, pattern in spec.formats.items():
                for cell in sheet[column][1:]:
                    cell.number_format = pattern
                sheet.column_dimensions[column].width = 16
        self.book.save(self.path)
