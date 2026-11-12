"""Подсчёты: периоды, выплаты, месяцы, статистика, данные для приложения."""
import calendar
from collections import defaultdict
from datetime import date, timedelta

from calc import DEFAULT_RATES, MODELS, M1, period_for, shift_pay, shift_sales

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
          "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def money(x: float) -> str:
    s = f"{x:,.2f}".replace(",", " ")
    return "$" + (s[:-3] if s.endswith(".00") else s)


def d(x: date) -> str:
    return x.strftime("%d.%m")


def month_key(x: date) -> str:
    return x.strftime("%Y-%m")


def shift_month(key: str, n: int) -> str:
    y, m = map(int, key.split("-"))
    m += n
    y += (m - 1) // 12
    return f"{y:04d}-{(m - 1) % 12 + 1:02d}"


def month_range(key: str):
    y, m = map(int, key.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def shift_dict(r) -> dict:
    return {"id": r["id"], "model": r["model"], "day": r["day"], "regular": r["regular"], "subs": r["subs"],
            "bundles": r["bundles"], "r_regular": r["r_regular"], "r_subs": r["r_subs"],
            "r_bundles": r["r_bundles"], "bonus": bool(r["bonus"]), "note": r["note"],
            "sales": shift_sales(r), "pay": shift_pay(r)}


def adj_dict(r) -> dict:
    return {"id": r["id"], "model": r["model"], "day": r["day"], "amount": r["amount"], "note": r["note"]}


def summarize(shifts, adjs=()) -> dict:
    s = {"shifts": 0, "sales": 0.0, "pay": 0.0, "adj": 0.0, "bonus_shifts": 0,
         "regular": 0.0, "subs": 0.0, "bundles": 0.0, "best": None}
    for r in shifts:
        p = shift_pay(r)
        s["shifts"] += 1
        s["sales"] += shift_sales(r)
        s["pay"] += p
        s["bonus_shifts"] += r["bonus"]
        s["regular"] += r["regular"]; s["subs"] += r["subs"]; s["bundles"] += r["bundles"]
        if s["best"] is None or p > s["best"]["pay"]:
            s["best"] = {"day": r["day"], "pay": round(p, 2)}
    s["adj"] = round(sum(a["amount"] for a in adjs), 2)
    s["pay"] = round(s["pay"] + s["adj"], 2)
    s["sales"] = round(s["sales"], 2)
    return s


# ---------- периоды и выплаты ----------
def periods(db, uid, today: date) -> list:
    """Все периоды выплат с первой смены до текущего включительно."""
    paid = db.paid_map(uid)
    cutoff = db.setting(uid, "imported_paid_before")   # выплаты из старой таблицы: дата неизвестна
    out = []
    for m in MODELS:
        first = db.first_day(uid, m)
        if not first:
            continue
        start, end = period_for(m, first)
        while start <= today:
            s = summarize(db.shifts(uid, m, start, end), db.adjustments(uid, m, start, end))
            paid_on = paid.get((m, start))
            status = "paid" if paid_on else ("current" if end >= today else "waiting")
            imported = bool(paid_on and cutoff and end.isoformat() < cutoff)
            out.append({"model": m, "start": start.isoformat(), "end": end.isoformat(),
                        "shifts": s["shifts"], "sales": s["sales"], "pay": s["pay"], "adj": s["adj"],
                        "status": status, "paid_on": paid_on.isoformat() if paid_on else None,
                        "imported": imported,
                        "wait_days": None if status == "current" or imported else ((paid_on or today) - end).days})
            start, end = period_for(m, end + timedelta(days=1))
    out.sort(key=lambda p: (p["end"], p["model"]))
    return out


def payouts(db, uid, today: date) -> dict:
    ps = [p for p in periods(db, uid, today) if p["pay"] > 0]
    waiting = [p for p in ps if p["status"] == "waiting"]
    paid = [p for p in ps if p["status"] == "paid"]
    delays = [p["wait_days"] for p in paid if p["wait_days"] is not None]
    return {"waiting": waiting, "current": [p for p in ps if p["status"] == "current"],
            "paid": sorted(paid, key=lambda p: p["paid_on"], reverse=True),
            "owed": round(sum(p["pay"] for p in waiting), 2),
            "avg_delay": round(sum(delays) / len(delays), 1) if delays else None}


# ---------- месяцы и статистика ----------
def month_summary(db, uid, key: str) -> dict:
    start, end = month_range(key)
    res = {m: summarize(db.shifts(uid, m, start, end), db.adjustments(uid, m, start, end)) for m in MODELS}
    res["total"] = round(sum(res[m]["pay"] for m in MODELS), 2)
    res["goal"] = db.get_goal(uid, key)
    return res


def all_months(db, uid, today: date) -> list:
    firsts = [x for x in (db.first_day(uid, m) for m in MODELS) if x]
    if not firsts:
        return []
    out, key, last = [], month_key(min(firsts)), month_key(today)
    while key <= last:
        s = month_summary(db, uid, key)
        out.append({"month": key, "total": s["total"], "goal": s["goal"],
                    **{m: {k: s[m][k] for k in ("pay", "sales", "shifts", "bonus_shifts", "adj")} for m in MODELS}})
        key = shift_month(key, 1)
    return out


def weekday_stats(db, uid) -> list:
    """Средний заработок по дням недели (по дням, когда были смены)."""
    per_day = defaultdict(float)
    for r in db.shifts(uid):
        per_day[r["day"]] += shift_pay(r)
    sums, cnt = [0.0] * 7, [0] * 7
    for day, pay in per_day.items():
        w = date.fromisoformat(day).weekday()
        sums[w] += pay; cnt[w] += 1
    return [{"weekday": WEEKDAYS[i], "avg": round(sums[i] / cnt[i], 2) if cnt[i] else 0, "days": cnt[i]}
            for i in range(7)]


def forecast(db, uid, today: date) -> dict:
    key = month_key(today)
    start, end = month_range(key)
    earned = month_summary(db, uid, key)["total"]
    elapsed = (today - start).days + 1
    total_days = end.day
    pace = earned / elapsed if elapsed else 0
    goal = db.get_goal(uid, key)
    left = max(0.0, (goal or 0) - earned)
    days_left = total_days - elapsed + 1
    return {"earned": earned, "projected": round(pace * total_days, 2), "pace": round(pace, 2),
            "goal": goal, "left": round(left, 2), "need_per_day": round(left / days_left, 2) if days_left else 0,
            "days_left": days_left}


def records(db, uid) -> dict:
    per_day = defaultdict(float)
    for r in db.shifts(uid):
        per_day[r["day"]] += shift_pay(r)
    best_day = max(per_day.items(), key=lambda x: x[1]) if per_day else None
    return {"best_day": {"day": best_day[0], "pay": round(best_day[1], 2)} if best_day else None,
            "total_earned": round(sum(per_day.values()), 2), "days_worked": len(per_day)}


def snapshot(db, uid, today: date, uah: float = None) -> dict:
    """Всё, что нужно приложению, одним ответом (данных немного)."""
    return {
        "today": today.isoformat(),
        "names": db.names(uid),
        "rates": {m: db.rates_for(uid, m, today) for m in MODELS},
        "rates_history": db.rates_history(uid),
        "rates_default": DEFAULT_RATES,
        "goals": db.goals(uid),
        "shifts": [shift_dict(r) for r in db.shifts(uid)],
        "adjustments": [adj_dict(r) for r in db.adjustments(uid)],
        "periods": periods(db, uid, today),
        "payouts": payouts(db, uid, today),
        "months": all_months(db, uid, today),
        "weekdays": weekday_stats(db, uid),
        "forecast": forecast(db, uid, today),
        "records": records(db, uid),
        "uah": uah,
    }


# ---------- тексты для бота ----------
def shift_text(names, model, day: date, regular, subs, bundles, rates: dict) -> str:
    row = {"regular": regular, "subs": subs, "bundles": bundles, **rates}
    pay = shift_pay(row)
    if model != M1:
        return (f"<b>{names[model]}</b> · смена {d(day)}\n"
                f"Продажи: {money(regular)} × {rates['r_regular']:g}%\n"
                f"💰 Мой заработок: <b>{money(pay)}</b>")
    lines = [f"<b>{names[model]}</b> · смена {d(day)}",
             f"Всего продаж: {money(regular + subs + bundles)}" + ("  🔥 бонус" if rates["bonus"] else ""),
             f"• обычные {money(regular)} × {rates['r_regular']:g}%",
             f"• сабки {money(subs)} × {rates['r_subs']:g}%",
             f"• бандлы {money(bundles)} × {rates['r_bundles']:g}%",
             f"💰 Мой заработок: <b>{money(pay)}</b>"]
    return "\n".join(lines)


def goal_line(db, uid, today: date) -> str:
    f = forecast(db, uid, today)
    if not f["goal"]:
        return f"🎯 Цель на {MONTHS[today.month - 1]} не задана"
    frac = f["earned"] / f["goal"]
    full = max(0, min(10, round(frac * 10)))
    line = f"🎯 {money(f['earned'])} из {money(f['goal'])}  {'▓' * full}{'░' * (10 - full)} {round(frac * 100)}%"
    if f["left"] > 0:
        line += f"\nОсталось {money(f['left'])} · ~{money(f['need_per_day'])} в день"
    else:
        line += "\n🏆 Цель выполнена!"
    return line


def period_report(db, uid, model, start: date) -> str:
    """Отчёт для сверки с овнером."""
    _, end = period_for(model, start)
    names = db.names(uid)
    shifts = db.shifts(uid, model, start, end)
    adjs = db.adjustments(uid, model, start, end)
    s = summarize(shifts, adjs)
    lines = [f"Отчёт {names[model]} · {start:%d.%m}–{end:%d.%m.%Y}", ""]
    for r in shifts:
        if model == M1:
            parts = [f"обычные {money(r['regular'])}×{r['r_regular']:g}%"]
            if r["subs"]:
                parts.append(f"сабки {money(r['subs'])}×{r['r_subs']:g}%")
            if r["bundles"]:
                parts.append(f"бандлы {money(r['bundles'])}×{r['r_bundles']:g}%")
            lines.append(f"{date.fromisoformat(r['day']):%d.%m}: {', '.join(parts)} = {money(shift_pay(r))}")
        else:
            lines.append(f"{date.fromisoformat(r['day']):%d.%m}: {money(r['regular'])}×{r['r_regular']:g}%"
                         f" = {money(shift_pay(r))}")
    for a in adjs:
        lines.append(f"{date.fromisoformat(a['day']):%d.%m}: {a['note'] or 'доп. начисление'} "
                     f"{'+' if a['amount'] >= 0 else '−'}{money(abs(a['amount']))}")
    lines += ["", f"Продажи: {money(s['sales'])} · смен: {s['shifts']}", f"Итого к выплате: {money(s['pay'])}"]
    return "\n".join(lines)
