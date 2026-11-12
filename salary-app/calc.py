"""Ставки, расчёт смены и периоды выплат. Без зависимостей."""
import calendar
from datetime import date, timedelta

# Две модели. Названия задаются в настройках приложения (не в коде).
M1 = "m1"   # обычные / сабки / бандлы + бонус за смену от порога
M2 = "m2"   # одна ставка от продаж
MODELS = (M1, M2)
DEFAULT_NAMES = {M1: "Модель 1", M2: "Модель 2"}

# Ставки в процентах. Меняются в приложении с датой начала действия.
DEFAULT_RATES = {
    M1: {"regular": 20, "subs": 25, "bundles": 30, "bonus_threshold": 300, "bonus_add": 5},
    M2: {"regular": 30},
}


def apply_rates(model: str, rates: dict, regular: float, subs: float = 0, bundles: float = 0) -> dict:
    """Какие ставки применить к смене (с учётом бонуса)."""
    if model == M2:
        return {"r_regular": rates["regular"], "r_subs": 0, "r_bundles": 0, "bonus": 0}
    threshold = rates.get("bonus_threshold") or 0
    bonus = bool(threshold) and (regular + subs + bundles) >= threshold
    return {
        "r_regular": rates["regular"] + (rates.get("bonus_add", 0) if bonus else 0),
        "r_subs": rates["subs"],
        "r_bundles": rates["bundles"],
        "bonus": int(bonus),
    }


def shift_pay(row) -> float:
    return round((row["regular"] * row["r_regular"] + row["subs"] * row["r_subs"]
                  + row["bundles"] * row["r_bundles"]) / 100, 2)


def shift_sales(row) -> float:
    return round(row["regular"] + row["subs"] + row["bundles"], 2)


# ---------- периоды выплат ----------
def _add_month(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def period_m2(d: date):
    """24–08 и 09–23 включительно."""
    if 9 <= d.day <= 23:
        return d.replace(day=9), d.replace(day=23)
    if d.day >= 24:
        return d.replace(day=24), _add_month(d, 1).replace(day=8)
    return _add_month(d, -1).replace(day=24), d.replace(day=8)


def period_m1(d: date):
    """1–10, 11–20, 21–конец месяца."""
    if d.day <= 10:
        return d.replace(day=1), d.replace(day=10)
    if d.day <= 20:
        return d.replace(day=11), d.replace(day=20)
    return d.replace(day=21), d.replace(day=calendar.monthrange(d.year, d.month)[1])


PERIOD_FUNCS = {M1: period_m1, M2: period_m2}


def period_for(model: str, d: date, offset: int = 0):
    start, end = PERIOD_FUNCS[model](d)
    for _ in range(-offset):
        start, end = PERIOD_FUNCS[model](start - timedelta(days=1))
    for _ in range(offset):
        start, end = PERIOD_FUNCS[model](end + timedelta(days=1))
    return start, end
