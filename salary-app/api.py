"""HTTP API для Mini App. Логика не зависит от веб-фреймворка; ниже — адаптер для aiohttp."""
import hashlib
import hmac
import json
import time
from datetime import date
from urllib.parse import parse_qsl

from calc import MODELS, M1, apply_rates
import service


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400):
    """Проверка подписи Telegram WebApp initData. Возвращает user dict или None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    if max_age and time.time() - int(pairs.get("auth_date", 0)) > max_age:
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None


# ---------- валидация ----------
def _num(v, name, allow_negative=False):
    try:
        x = round(float(str(v if v not in (None, "") else 0).replace(",", ".")), 2)
    except ValueError:
        raise ApiError(f"Неверное число: {name}")
    if x < 0 and not allow_negative:
        raise ApiError(f"{name}: не может быть отрицательным")
    if abs(x) > 1_000_000:
        raise ApiError(f"{name}: слишком большое число")
    return x


def _day(v):
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise ApiError("Неверная дата")


def _model(v):
    if v not in MODELS:
        raise ApiError("Неизвестная модель")
    return v


def _text(v, limit=300):
    return str(v or "").strip()[:limit]


# ---------- обработчик ----------
async def handle(db, uid: int, method: str, path: str, body: dict, today: date, uah=None, actions=None):
    """Возвращает dict-ответ. actions: объект с async send_text(uid, text) и send_excel(uid)."""
    body = body or {}
    if method == "GET" and path == "/api/all":
        return service.snapshot(db, uid, today, uah)
    if method != "POST":
        raise ApiError("Not found", 404)

    if path == "/api/shift":
        model, day = _model(body.get("model")), _day(body.get("day"))
        regular = _num(body.get("regular"), "продажи")
        subs = _num(body.get("subs"), "сабки") if model == M1 else 0.0
        bundles = _num(body.get("bundles"), "бандлы") if model == M1 else 0.0
        note = _text(body.get("note"))
        if body.get("id"):
            if not db.update_shift(uid, int(body["id"]), model, day, regular, subs, bundles, note):
                raise ApiError("Смена не найдена", 404)
        else:
            db.add_shift(uid, model, day, regular, subs, bundles, note)
    elif path == "/api/shift/delete":
        db.delete_shift(uid, int(body.get("id", 0)))
    elif path == "/api/adjustment":
        amount = _num(body.get("amount"), "сумма", allow_negative=True)
        if amount == 0:
            raise ApiError("Сумма не может быть нулевой")
        db.add_adjustment(uid, _model(body.get("model")), _day(body.get("day")), amount, _text(body.get("note")))
    elif path == "/api/adjustment/delete":
        db.delete_adjustment(uid, int(body.get("id", 0)))
    elif path == "/api/payout":
        model, start = _model(body.get("model")), _day(body.get("start"))
        if body.get("paid_on"):
            db.mark_paid(uid, model, start, _day(body["paid_on"]))
        else:
            db.unmark_paid(uid, model, start)
    elif path == "/api/goal":
        amount = _num(body.get("amount"), "цель")
        month = _text(body.get("month"), 7)
        _day(month + "-01")
        db.set_goal(uid, month, amount)
    elif path == "/api/names":
        names = {m: _text(body.get(m), 30) for m in MODELS if _text(body.get(m), 30)}
        db.set_setting(uid, "names", {**db.names(uid), **names})
    elif path == "/api/rates":
        model, valid_from = _model(body.get("model")), _day(body.get("valid_from"))
        keys = ["regular", "subs", "bundles", "bonus_threshold", "bonus_add"] if model == M1 else ["regular"]
        data = {k: _num((body.get("data") or {}).get(k), k) for k in keys}
        if any(data[k] > 100 for k in keys if k != "bonus_threshold"):
            raise ApiError("Ставка не может быть больше 100%")
        db.set_rates(uid, model, valid_from, data)
        if body.get("recalc"):
            for r in db.shifts(uid, model, valid_from):
                db.update_shift(uid, r["id"], model, date.fromisoformat(r["day"]),
                                r["regular"], r["subs"], r["bundles"], r["note"])
    elif path == "/api/rates/delete":
        db.delete_rates(uid, _model(body.get("model")), _text(body.get("valid_from"), 10))
    elif path == "/api/report":
        model, start = _model(body.get("model")), _day(body.get("start"))
        text = service.period_report(db, uid, model, start)
        if actions and not body.get("dry"):
            await actions.send_text(uid, text)
        return {"ok": True, "text": text}
    elif path == "/api/excel":
        if actions:
            await actions.send_excel(uid)
        return {"ok": True}
    elif path == "/api/preview":
        model, day = _model(body.get("model")), _day(body.get("day"))
        regular = _num(body.get("regular"), "продажи")
        subs = _num(body.get("subs"), "сабки") if model == M1 else 0.0
        bundles = _num(body.get("bundles"), "бандлы") if model == M1 else 0.0
        return apply_rates(model, db.rates_for(uid, model, day), regular, subs, bundles)
    else:
        raise ApiError("Not found", 404)
    if actions and hasattr(actions, "changed"):
        await actions.changed(uid)
    return service.snapshot(db, uid, today, uah)


# ---------- адаптер aiohttp ----------
def make_aiohttp_app(db, token, allowed: set, today_fn, uah_fn, actions):
    from aiohttp import web

    cors = {"Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, X-Init-Data",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS"}

    async def endpoint(request):
        if request.method == "OPTIONS":
            return web.Response(headers=cors)
        user = verify_init_data(request.headers.get("X-Init-Data", ""), token)
        if not user or (allowed and user.get("id") not in allowed):
            return web.json_response({"error": "Нет доступа. Открой приложение из бота."}, status=401,
                                     headers=cors)
        try:
            body = await request.json() if request.method == "POST" and request.can_read_body else {}
            res = await handle(db, int(user["id"]), request.method, request.path, body,
                               today_fn(), await uah_fn(), actions)
            return web.json_response(res, headers=cors)
        except ApiError as e:
            return web.json_response({"error": str(e)}, status=e.status, headers=cors)
        except (ValueError, TypeError) as e:
            return web.json_response({"error": f"Неверные данные: {e}"}, status=400, headers=cors)

    async def health(_):
        return web.Response(text="ok", headers=cors)

    app = web.Application(client_max_size=64 * 1024)
    app.router.add_route("*", "/api/{tail:.*}", endpoint)
    app.router.add_get("/health", health)
    return app
