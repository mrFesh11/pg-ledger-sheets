from datetime import UTC, date, datetime, timedelta

from pg_ledger_sheets.core import Cell, Record, State, plan
from pg_ledger_sheets.profiles.bedolaga import (
    BONUS,
    MANUAL,
    PAYMENT,
    PAYMENTS,
    PROFILE,
    PURCHASE,
    PURCHASES,
    REFERRAL,
    REFUND,
    SUMMARY_HEADER,
    TOTAL,
    Tx,
    classify,
    expense_pivot,
    method_pivot,
    month_row,
    purchase_row,
    revenue_kopeks,
    summary_total,
    tariff_of,
    tx_of,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def record(
    id: int,
    type: str = "deposit",
    amount: int = 10000,
    method: str | None = "yookassa",
    at: datetime = NOW,
    description: str = "Пополнение",
    done: bool = True,
    telegram_id: int | None = 111,
) -> Record:
    data = {"type": type, "amount_kopeks": amount, "description": description, "payment_method": method}
    return Record(id, at, done, {**data, "user_id": 7, "telegram_id": telegram_id, "username": "vasya", "email": "v@example.com"})


def tx(id: int, **fields: object) -> Tx:
    return tx_of(record(id, **fields))  # type: ignore[arg-type]


def payments(records: list[Record], **options: object) -> list[list[Cell]]:
    return plan(PROFILE, State(), records, NOW, lambda day: 0, **options).rows[PAYMENTS]  # type: ignore[arg-type]


def test_classify():
    assert classify(tx(1)) == PAYMENT
    assert classify(tx(1, method="yookassa_sbp")) == PAYMENT
    assert classify(tx(1, type="subscription_payment", amount=-500, method="platega")) == PAYMENT
    assert classify(tx(1, type="subscription_payment", amount=-500, method="balance")) == PURCHASE
    assert classify(tx(1, type="subscription_payment", amount=-500, method=None)) == PURCHASE
    assert classify(tx(1, method="manual")) == MANUAL
    assert classify(tx(1, method=None, description="Бонус по промокоду X")) == BONUS
    assert classify(tx(1, method=None, description="Реферальный бонус")) == REFERRAL
    assert classify(tx(1, type="referral_reward", method=None)) == REFERRAL
    assert classify(tx(1, type="poll_reward", method=None)) == BONUS
    assert classify(tx(1, type="refund", method="apple_iap")) == REFUND
    assert classify(tx(1, type="refund", method=None)) is None
    assert classify(tx(1, type="refund", method="balance")) is None
    assert classify(tx(1, type="withdrawal", method=None)) is None


def test_revenue():
    assert revenue_kopeks(tx(1, type="subscription_payment", amount=-500, method="platega")) == 500
    assert revenue_kopeks(tx(1, type="refund", amount=300, method="apple_iap")) == -300
    assert revenue_kopeks(tx(1, method="manual")) == 0
    assert revenue_kopeks(tx(1, type="subscription_payment", amount=-500, method="balance")) == 0


def test_month_formulas():
    row = month_row(date(2026, 11, 1), 4, ";")
    assert row[1] == "=SUMIFS('Оплаты'!I:I;'Оплаты'!A:A;\">=\"&A4;'Оплаты'!A:A;\"<\"&EDATE(A4;1);'Оплаты'!E:E;\"Оплата\")"
    assert row[3] == "=B4+C4"
    assert row[4] == "=SUMIFS('Расходы'!E:E;'Расходы'!A:A;\">=\"&A4;'Расходы'!A:A;\"<\"&EDATE(A4;1))"
    assert row[5] == "=D4-E4"
    assert row[6] == "=COUNTIFS('Оплаты'!A:A;\">=\"&A4;'Оплаты'!A:A;\"<\"&EDATE(A4;1);'Оплаты'!E:E;\"Оплата\")"
    assert row[7] == "=IFERROR(B4/G4;0)"
    assert len(row) == len(SUMMARY_HEADER) == len(summary_total(";"))
    assert summary_total(";")[1] == "=SUM(B3:B1000)"


def test_pivots():
    assert method_pivot(";") == (
        "=IFERROR(QUERY('Оплаты'!A:I;\"select year(A), month(A)+1, sum(I) where E = 'Оплата' group by year(A), month(A)+1 pivot F "
        "label year(A) 'Год', month(A)+1 'Месяц'\";1);\"Пока нет данных\")"
    )
    assert "'Расходы'!A:E" in expense_pivot(";") and "sum(E)" in expense_pivot(";") and "pivot B" in expense_pivot(";")


def test_tariff_names():
    cases = {
        "Покупка тарифа 'Базовый' на 30 дней (скидка 5%) (промо -10%)": "Базовый",
        "Покупка тарифа Без лимита на 30 дней": "Без лимита",
        "Продление подписки на 30 дней (Макси 2)": "Макси 2",
        "Продление подписки на 1 день (Базовый)": "Базовый",
        "Смена тарифа (массовое действие) на 'Базовый'": "Базовый",
        "Переход на тариф 'Расширенный' (доплата за 12 дней)": "Расширенный",
        "Gift: Без лимита (30d)": "Без лимита",
        "Автопродление подписки на 30 дней": "",
        "Покупка 2 доп. устройств (скидка 5%)": "",
        "Докупка 50 ГБ трафика (скидка 5%)": "",
    }
    assert {text: tariff_of(text) for text in cases} == cases
    assert purchase_row(tx(8, type="gift_payment", method="balance", description="Gift: Базовый (30d)"))[5] == 30


def test_rows_and_msk_date():
    row = payments([record(1, at=datetime(2026, 11, 10, 21, 30, tzinfo=UTC))])[0]
    assert row[0] == date(2026, 11, 11)
    assert str(row[1]) == "00:30:00"
    assert row[2:6] == [111, "@vasya", PAYMENT, "ЮKassa"]
    assert row[7:] == [100.0, 100.0, 1]


def test_email_only_client():
    assert payments([record(1, telegram_id=None)])[0][2] == "v@example.com"


def test_refund_is_negative():
    assert payments([record(1, type="refund", amount=300, method="apple_iap")])[0][7:9] == [-3.0, -3.0]


def test_manual_credit_is_listed_but_not_revenue():
    d9 = NOW - timedelta(days=2)
    records = [record(1, at=d9), record(2, at=d9, method="manual"), record(3)]
    rows = plan(PROFILE, State(), records, NOW, lambda day: sum(PROFILE.revenue(r) for r in records if r.at.date() == day)).rows[PAYMENTS]
    assert [row[4] for row in rows] == [PAYMENT, MANUAL, TOTAL, PAYMENT]
    assert rows[1][7:9] == [100.0, 0.0]
    assert rows[2][6] == "ИТОГО ЗА ДЕНЬ 09.11.2026"
    assert rows[2][8] == 100.0


def test_purchases_go_to_separate_sheet():
    buy = record(5, type="subscription_payment", amount=-99900, method="balance", description="Покупка тарифа 'Стандартный' на 365 дней (скидка 5%)")
    batch = plan(PROFILE, State(), [buy], NOW, lambda day: 0)
    assert batch.rows[PAYMENTS] == []
    assert batch.rows[PURCHASES][0][4:7] == ["Стандартный", 365, 999.0]
    assert purchase_row(tx(6, type="subscription_payment", description="Покупка подписки через лендинг (Семейный 5, 30 дн.)"))[4:6] == ["Семейный 5", 30]
    assert purchase_row(tx(7, type="subscription_payment", description="Продление подписки на 30 дней"))[4:6] == ["", 30]


def test_profile_shape():
    assert PROFILE.sheets[0].header[PROFILE.kind_column] == "Тип"
    assert len(PROFILE.total_row(date(2026, 11, 9), 0)) == len(PROFILE.sheets[0].header)
    assert all(sheet.header[-1] == "ID операции" for sheet in PROFILE.sheets)


def test_profile_validation():
    from dataclasses import replace

    import pytest

    with pytest.raises(ValueError):
        replace(PROFILE, kind_column=3)
    with pytest.raises(ValueError):
        replace(PROFILE, ledger="Нет такого")
    with pytest.raises(ValueError):
        plan(replace(PROFILE, route=lambda r: (PAYMENTS, [r.at.date(), r.id])), State(), [record(1)], NOW, lambda day: 0)
