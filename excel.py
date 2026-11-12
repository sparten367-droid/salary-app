"""Выгрузка всех данных в Excel (пересобирается целиком из базы)."""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from calc import M1, M2, period_for, shift_pay, shift_sales
import service

USD = '"$"#,##0.00'
HEAD = PatternFill("solid", fgColor="DDE7F5")
BONUS = PatternFill("solid", fgColor="CCF5F5")
OWED = PatternFill("solid", fgColor="FCE4E4")
PAID = PatternFill("solid", fgColor="E2F0D9")


def _sheet(wb, title, headers, widths, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.font, c.fill = Font(bold=True), HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    return ws


def _fmt(ws, cols, fmt=USD):
    for c in cols:
        ws.cell(row=ws.max_row, column=c).number_format = fmt


def _period(model, day):
    s, e = period_for(model, day)
    return f"{s:%d.%m}–{e:%d.%m.%Y}"


def build(db, uid, today: date) -> Workbook:
    names = db.names(uid)
    wb = Workbook()

    ws = _sheet(wb, names[M1], ["Дата", "Обычные", "%", "Сабки", "%", "Бандлы", "%", "Всего продаж",
                                "Бонус", "Заработок", "Период", "Заметка"],
                [12, 11, 6, 11, 6, 11, 6, 13, 8, 12, 20, 30], first=True)
    for r in db.shifts(uid, M1):
        day = date.fromisoformat(r["day"])
        ws.append([day, r["regular"], r["r_regular"], r["subs"], r["r_subs"], r["bundles"], r["r_bundles"],
                   shift_sales(r), "да" if r["bonus"] else "", shift_pay(r), _period(M1, day), r["note"]])
        ws.cell(row=ws.max_row, column=1).number_format = "DD.MM.YYYY"
        _fmt(ws, [2, 4, 6, 8, 10])
        if r["bonus"]:
            ws.cell(row=ws.max_row, column=2).fill = BONUS

    ws = _sheet(wb, names[M2], ["Дата", "Продажи", "%", "Заработок", "Период", "Заметка"], [12, 12, 6, 12, 20, 30])
    for r in db.shifts(uid, M2):
        day = date.fromisoformat(r["day"])
        ws.append([day, r["regular"], r["r_regular"], shift_pay(r), _period(M2, day), r["note"]])
        ws.cell(row=ws.max_row, column=1).number_format = "DD.MM.YYYY"
        _fmt(ws, [2, 4])

    ws = _sheet(wb, "Доп. начисления", ["Дата", "Модель", "Сумма", "Комментарий"], [12, 12, 12, 40])
    for a in db.adjustments(uid):
        ws.append([date.fromisoformat(a["day"]), names[a["model"]], a["amount"], a["note"]])
        ws.cell(row=ws.max_row, column=1).number_format = "DD.MM.YYYY"
        _fmt(ws, [3])

    ws = _sheet(wb, "Выплаты", ["Модель", "Период", "К выплате", "Статус", "Дата выплаты", "Ждали дней"],
                [12, 22, 12, 12, 14, 12])
    labels = {"paid": "выплачено", "waiting": "ждём", "current": "идёт"}
    for p in service.periods(db, uid, today):
        if p["pay"] <= 0:
            continue
        s, e = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
        ws.append([names[p["model"]], f"{s:%d.%m}–{e:%d.%m.%Y}", p["pay"], labels[p["status"]],
                   date.fromisoformat(p["paid_on"]) if p["paid_on"] else None, p["wait_days"]])
        ws.cell(row=ws.max_row, column=5).number_format = "DD.MM.YYYY"
        _fmt(ws, [3])
        fill = {"paid": PAID, "waiting": OWED}.get(p["status"])
        if fill:
            ws.cell(row=ws.max_row, column=4).fill = fill

    ws = _sheet(wb, "Месяцы", ["Месяц", names[M1], names[M2], "Итого", "Цель", "Выполнено"],
                [16, 12, 12, 12, 12, 12])
    for m in service.all_months(db, uid, today):
        y, mo = map(int, m["month"].split("-"))
        ws.append([f"{service.MONTHS[mo - 1].capitalize()} {y}", m[M1]["pay"], m[M2]["pay"], m["total"],
                   m["goal"], (m["total"] / m["goal"]) if m["goal"] else None])
        _fmt(ws, [2, 3, 4, 5])
        ws.cell(row=ws.max_row, column=6).number_format = "0%"
        ws.cell(row=ws.max_row, column=4).font = Font(bold=True)
    return wb


def to_bytes(db, uid, today: date) -> bytes:
    buf = io.BytesIO()
    build(db, uid, today).save(buf)
    return buf.getvalue()


def write(db, uid, path: str, today: date) -> bool:
    """False, если файл открыт в Excel (Windows его блокирует)."""
    data = to_bytes(db, uid, today)
    try:
        with open(path, "wb") as f:
            f.write(data)
        return True
    except PermissionError:
        return False
