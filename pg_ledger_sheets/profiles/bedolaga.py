import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..core import SUMMARY_FIRST_MONTH_ROW, Cell, Profile, Record, Sheet

MSK = ZoneInfo("Europe/Moscow")

SOURCE = """
    select t.id, coalesce(t.completed_at, t.created_at) as at, t.is_completed as done,
           t.type, t.amount_kopeks, coalesce(t.description, '') as description, t.payment_method,
           t.user_id, u.telegram_id, u.username, u.email
    from transactions t join users u on u.id = t.user_id
"""

PAYMENTS = "Оплаты"
PURCHASES = "Покупки"
EXPENSES = "Расходы"
SUMMARY = "Итоги"
BY_METHOD = "По способам оплаты"
BY_EXPENSE = "По статьям расходов"

PAYMENT = "Оплата"
REFUND = "Возврат"
BONUS = "Бонус"
REFERRAL = "Рефералка"
MANUAL = "Ручное зачисление"
PURCHASE = "Покупка"
TOTAL = "Итого"

DAY = "dd.mm.yyyy"
CLOCK = "hh:mm"
WHOLE = "0"
MONEY = "#,##0.00"
MONTH = "mm.yyyy"
PIVOT_FORMATS = {column: MONEY for column in "CDEFGHIJKLMNOPQRSTUVWXYZ"}

NON_GATEWAY = frozenset({"manual", "balance"})
MONEY_IN = frozenset({"deposit", "subscription_payment", "gift_payment"})
REFERRAL_RE = re.compile(r"реферал|referral", re.IGNORECASE)
TARIFF_RES = [
    re.compile(pattern)
    for pattern in (
        r"тарифа\s+'?(.+?)'?\s+на\s+\d+",
        r"на\s+'(.+?)'",
        r"тариф\s+'(.+?)'",
        r"\((.+?),\s*\d+\s*дн",
        r"(?:дн\w*|день)\s+\((?!скидка|промо)([^)]+)\)",
        r"Gift:\s*(.+?)\s*\(",
    )
]
DAYS_RE = re.compile(r"(\d+)\s*(?:дн|день|дня|d\))")

METHOD_LABELS = {
    "yookassa": "ЮKassa",
    "yookassa_sbp": "ЮKassa СБП",
    "telegram_stars": "Telegram Stars",
    "stars": "Telegram Stars",
    "cryptobot": "CryptoBot",
    "platega": "Platega",
    "platega_recurrent": "Platega автоплатёж",
    "manual": "Админ",
    "balance": "Баланс",
}

PAYMENTS_HEADER = ["Дата", "Время", "Telegram ID", "Юзернейм", "Тип", "Способ оплаты", "Описание", "Сумма, ₽", "В выручку, ₽", "ID операции"]
PURCHASES_HEADER = ["Дата", "Время", "Telegram ID", "Юзернейм", "Тариф", "Срок, дней", "Сумма, ₽", "Описание", "ID операции"]
EXPENSES_HEADER = ["Дата", "Статья", "Что оплачено", "Кому", "Сумма, ₽", "Комментарий"]
SUMMARY_HEADER = [
    "Месяц",
    "Доходы (оплаты), ₽",
    "Возвраты, ₽",
    "Выручка, ₽",
    "Расходы, ₽",
    "Прибыль, ₽",
    "Оплат, шт.",
    "Средний чек, ₽",
    "Бонусы, ₽",
    "Рефералка, ₽",
    "Ручные зачисления, ₽",
]


@dataclass(frozen=True)
class Tx:
    id: int
    type: str
    amount_kopeks: int
    description: str
    payment_method: str | None
    at: datetime
    user_id: int
    telegram_id: int | None
    username: str | None
    email: str | None


def tx_of(record: Record) -> Tx:
    data = record.data
    return Tx(
        record.id,
        data["type"],
        data["amount_kopeks"],
        data["description"],
        data["payment_method"],
        record.at,
        data["user_id"],
        data["telegram_id"],
        data["username"],
        data["email"],
    )


def tariff_of(description: str) -> str:
    for pattern in TARIFF_RES:
        if found := pattern.search(description):
            return found.group(1)
    return ""


def classify(tx: Tx) -> str | None:
    real = tx.payment_method is not None and tx.payment_method not in NON_GATEWAY
    if tx.type in MONEY_IN and real:
        return PAYMENT
    if tx.type == "refund":
        return REFUND if real else None
    if tx.type == "referral_reward":
        return REFERRAL
    if tx.type == "poll_reward":
        return BONUS
    if tx.type == "deposit":
        if tx.payment_method == "manual":
            return MANUAL
        return REFERRAL if REFERRAL_RE.search(tx.description) else BONUS
    if tx.type in ("subscription_payment", "gift_payment"):
        return PURCHASE
    return None


def revenue_kopeks(tx: Tx) -> int:
    kind = classify(tx)
    if kind == PAYMENT:
        return abs(tx.amount_kopeks)
    if kind == REFUND:
        return -abs(tx.amount_kopeks)
    return 0


def rub(kopeks: int) -> float:
    return round(kopeks / 100, 2)


def _who(tx: Tx) -> tuple[Cell, str]:
    client: Cell = tx.telegram_id if tx.telegram_id is not None else (tx.email or f"id{tx.user_id}")
    return client, f"@{tx.username}" if tx.username else ""


def _when(tx: Tx) -> tuple[date, time]:
    local = tx.at.astimezone(MSK)
    return local.date(), local.time().replace(microsecond=0, tzinfo=None)


def payment_row(tx: Tx, kind: str) -> list[Cell]:
    day, clock = _when(tx)
    client, username = _who(tx)
    amount = -abs(tx.amount_kopeks) if kind == REFUND else abs(tx.amount_kopeks)
    method = METHOD_LABELS.get(tx.payment_method or "", tx.payment_method or "")
    return [day, clock, client, username, kind, method, tx.description, rub(amount), rub(revenue_kopeks(tx)), tx.id]


def purchase_row(tx: Tx) -> list[Cell]:
    day, clock = _when(tx)
    client, username = _who(tx)
    days = DAYS_RE.search(tx.description)
    return [
        day,
        clock,
        client,
        username,
        tariff_of(tx.description),
        int(days.group(1)) if days else "",
        rub(abs(tx.amount_kopeks)),
        tx.description,
        tx.id,
    ]


def route(record: Record) -> tuple[str, list[Cell]] | None:
    tx = tx_of(record)
    kind = classify(tx)
    if kind is None:
        return None
    if kind == PURCHASE:
        return PURCHASES, purchase_row(tx)
    return PAYMENTS, payment_row(tx, kind)


def revenue(record: Record) -> int:
    return revenue_kopeks(tx_of(record))


def total_row(day: date, kopeks: int) -> list[Cell]:
    return [day, "", "", "", TOTAL, "", f"ИТОГО ЗА ДЕНЬ {day:%d.%m.%Y}", "", rub(kopeks), ""]


def month_row(month: date, row: int, sep: str) -> list[Cell]:
    period = [f"'{PAYMENTS}'!A:A", f'">="&A{row}', f"'{PAYMENTS}'!A:A", f'"<"&EDATE(A{row}{sep}1)']

    def sumifs(column: str, kind: str) -> str:
        return "=SUMIFS(" + sep.join([f"'{PAYMENTS}'!{column}:{column}", *period, f"'{PAYMENTS}'!E:E", f'"{kind}"']) + ")"

    expenses = "=SUMIFS(" + sep.join([f"'{EXPENSES}'!E:E", f"'{EXPENSES}'!A:A", f'">="&A{row}', f"'{EXPENSES}'!A:A", f'"<"&EDATE(A{row}{sep}1)']) + ")"
    count = "=COUNTIFS(" + sep.join([*period, f"'{PAYMENTS}'!E:E", f'"{PAYMENT}"']) + ")"
    return [
        month,
        sumifs("I", PAYMENT),
        sumifs("I", REFUND),
        f"=B{row}+C{row}",
        expenses,
        f"=D{row}-E{row}",
        count,
        f"=IFERROR(B{row}/G{row}{sep}0)",
        sumifs("H", BONUS),
        sumifs("H", REFERRAL),
        sumifs("H", MANUAL),
    ]


def summary_total(sep: str) -> list[Cell]:
    def total(column: str) -> str:
        return f"=SUM({column}{SUMMARY_FIRST_MONTH_ROW}:{column}1000)"

    return ["ВСЕГО", *(total(c) for c in "BCDEFG"), f"=IFERROR(B2/G2{sep}0)", *(total(c) for c in "IJK")]


def _pivot(source: str, value: str, column: str, where: str, sep: str) -> str:
    query = f"select year(A), month(A)+1, sum({value}) where {where} group by year(A), month(A)+1 pivot {column} label year(A) 'Год', month(A)+1 'Месяц'"
    return f'=IFERROR(QUERY({source}{sep}"{query}"{sep}1){sep}"Пока нет данных")'


def method_pivot(sep: str) -> str:
    return _pivot(f"'{PAYMENTS}'!A:I", "I", "F", f"E = '{PAYMENT}'", sep)


def expense_pivot(sep: str) -> str:
    return _pivot(f"'{EXPENSES}'!A:E", "E", "B", "A is not null and B is not null", sep)


PROFILE = Profile(
    source=SOURCE,
    timezone=MSK,
    locale="ru_RU",
    separator=";",
    sheets=[
        Sheet(PAYMENTS, PAYMENTS_HEADER, {"A": DAY, "B": CLOCK, "C": WHOLE, "H": MONEY, "I": MONEY}),
        Sheet(PURCHASES, PURCHASES_HEADER, {"A": DAY, "B": CLOCK, "C": WHOLE, "G": MONEY}),
    ],
    ledger=PAYMENTS,
    kind_column=4,
    total_label=TOTAL,
    summary=Sheet(SUMMARY, SUMMARY_HEADER, {"A": MONTH, **{column: MONEY for column in "BCDEFHIJK"}, "G": WHOLE}),
    extra=[
        Sheet(EXPENSES, EXPENSES_HEADER, {"A": DAY, "E": MONEY}),
        Sheet(BY_METHOD, formats=PIVOT_FORMATS, formula=method_pivot),
        Sheet(BY_EXPENSE, formats=PIVOT_FORMATS, formula=expense_pivot),
    ],
    route=route,
    revenue=revenue,
    total_row=total_row,
    summary_total=summary_total,
    month_row=month_row,
)
