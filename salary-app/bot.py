"""Telegram-бот + API для Mini App (aiogram 3 + aiohttp)."""
import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, MenuButtonWebApp, Message, ReplyKeyboardMarkup, WebAppInfo)

import excel
import service
from api import make_aiohttp_app
from calc import M1, M2, MODELS, apply_rates, period_for
from db import DB
from service import MONTHS, d, goal_line, money, month_key

BASE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(BASE, ".env"))
TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
API_PORT = int(os.environ.get("API_PORT", "8787"))
NOTIFY_HOUR = int(os.environ.get("NOTIFY_HOUR", "10"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE, "salary.db"))
EXCEL_PATH = os.environ.get("EXCEL_PATH", os.path.join(BASE, "salary.xlsx"))
try:
    TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Kiev"))
except Exception:
    TZ = None
DEFAULT_GOAL = 1500

db = DB(DB_PATH)
dp = Dispatcher()
if ALLOWED:
    dp.message.filter(F.from_user.id.in_(ALLOWED))
    dp.callback_query.filter(F.from_user.id.in_(ALLOWED))

state_app = {"url": None}        # адрес Mini App (с туннелем), когда готов
excel_pending: set[int] = set()
bot_ref: dict = {}


def today() -> date:
    return datetime.now(TZ).date()


def app_url():
    return state_app["url"]


# ================= курс гривны (НБУ) =================
_uah = {"rate": None, "at": 0}


async def uah_rate():
    if time.time() - _uah["at"] < 6 * 3600:
        return _uah["rate"]
    _uah["at"] = time.time()
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get("https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=USD&json") as r:
                _uah["rate"] = round(float((await r.json(content_type=None))[0]["rate"]), 2)
    except Exception:
        logging.warning("не удалось получить курс НБУ")
    return _uah["rate"]


# ================= клавиатуры =================
B_APP, B_PAY, B_XLS = "📱 Приложение", "💸 Выплаты", "📁 Таблица"


def menu(uid):
    n = db.names(uid)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=B_APP)],
        [KeyboardButton(text=f"➕ {n[M1]}"), KeyboardButton(text=f"➕ {n[M2]}")],
        [KeyboardButton(text=B_PAY), KeyboardButton(text=B_XLS)],
    ], resize_keyboard=True, is_persistent=True)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row] for row in rows if row])


def app_button(text="📱 Открыть приложение"):
    url = app_url()
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]) \
        if url else None


CANCEL = ("❌ Отмена", "cancel")
MENU_PREFIXES = ("➕ ",)
MENU_TEXTS = {B_APP, B_PAY, B_XLS}
NOT_MENU = ~F.text.in_(MENU_TEXTS) & ~F.text.startswith("➕ ")


async def edit(msg: Message, text: str, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await msg.answer(text, reply_markup=markup)


# ================= таблица и действия из приложения =================
async def refresh_excel(uid: int, notify_msg: Message | None = None):
    ok = await asyncio.to_thread(excel.write, db, uid, EXCEL_PATH, today())
    if ok:
        excel_pending.discard(uid)
    else:
        first = uid not in excel_pending
        excel_pending.add(uid)
        if notify_msg and first:
            await notify_msg.answer("📁 Таблица открыта в Excel — обновлю её, когда закроешь.")


class Actions:
    async def send_text(self, uid, text):
        await bot_ref["bot"].send_message(uid, text, parse_mode=None)

    async def send_excel(self, uid):
        data = await asyncio.to_thread(excel.to_bytes, db, uid, today())
        await bot_ref["bot"].send_document(uid, BufferedInputFile(data, f"salary_{today():%Y-%m-%d}.xlsx"),
                                           caption="📁 Все смены, выплаты и месяцы")

    async def changed(self, uid):
        await refresh_excel(uid)


# ================= старт =================
HELP = ("Привет! Я считаю твою ЗП 💰\n\n"
        "📱 <b>Приложение</b> — календарь, статистика, выплаты, настройки\n"
        "➕ — быстро внести смену прямо в чате\n"
        "💸 <b>Выплаты</b> — что овнер ещё должен\n"
        "📁 <b>Таблица</b> — Excel со всеми данными\n\n"
        "Быстрый ввод строкой: <code>1 200 50 100</code> (первая модель: обычные, сабки, бандлы) "
        "или <code>2 450</code> (вторая модель), в конце можно дату: <code>2 450 11.11</code>")


@dp.message(CommandStart())
@dp.message(Command("help", "menu"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(HELP, reply_markup=menu(msg.from_user.id))
    if app_url():
        await msg.answer("Всё самое удобное — в приложении 👇", reply_markup=app_button())
    if not db.get_goal(msg.from_user.id, month_key(today())):
        await ask_goal(msg, month_key(today()))


@dp.message(F.text == B_APP)
@dp.message(Command("app"))
async def open_app(msg: Message, state: FSMContext):
    await state.clear()
    if app_url():
        await msg.answer("Открывай 👇", reply_markup=app_button())
    else:
        await msg.answer("⚠️ Приложение сейчас недоступно: не запустился туннель. "
                         "Перезапусти бота (stop_bot.bat → start_bot.bat). Внести смену можно кнопками ➕.")


@dp.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(call.message, "Отменено.")
    await call.answer()


# ================= ввод смены =================
class Add(StatesGroup):
    date = State()
    amount = State()
    confirm = State()


STEPS = {M1: ["обычных продаж", "сабок", "бандлов"], M2: ["продаж"]}


def day_kb():
    t = today()
    return kb([(f"Сегодня, {d(t)}", "day:0"), (f"Вчера, {d(t - timedelta(days=1))}", "day:1")],
              [("📅 Другая дата", "day:other")], [CANCEL])


@dp.message(F.text.startswith("➕ "))
async def add_start(msg: Message, state: FSMContext):
    names = db.names(msg.from_user.id)
    model = next((m for m in MODELS if msg.text == f"➕ {names[m]}"), None)
    if not model:
        await msg.answer("Обновил меню 👇", reply_markup=menu(msg.from_user.id))
        return
    await state.clear()
    await state.set_state(Add.date)
    await state.update_data(model=model, nums=[])
    await msg.answer(f"➕ <b>{names[model]}</b> — за какой день смена?", reply_markup=day_kb())


@dp.callback_query(StateFilter(Add.date), F.data.startswith("day:"))
async def add_day(call: CallbackQuery, state: FSMContext):
    arg = call.data.split(":")[1]
    if arg == "other":
        await edit(call.message, "Напиши дату смены в формате <b>ДД.ММ</b>, например 09.11", kb([CANCEL]))
    else:
        await state.update_data(day=(today() - timedelta(days=int(arg))).isoformat())
        await ask_amount(call.message, call.from_user.id, state, edit_msg=True)
    await call.answer()


def parse_date(text: str) -> date:
    t = today()
    parts = text.strip().replace("/", ".").split(".")
    try:
        dd, mm = int(parts[0]), int(parts[1])
        yy = int(parts[2]) + (2000 if len(parts) > 2 and int(parts[2]) < 100 else 0) if len(parts) > 2 and parts[2] else t.year
        res = date(yy, mm, dd)
    except (ValueError, IndexError):
        raise ValueError("Дата в формате ДД.ММ, например 09.11")
    if len(parts) < 3 and res > t + timedelta(days=7):
        res = res.replace(year=res.year - 1)
    return res


def parse_amount(text: str) -> float:
    try:
        v = round(float(text.strip().replace(" ", "").replace("$", "").replace(",", ".")), 2)
    except ValueError:
        raise ValueError("Нужно число, например 250 или 99.5")
    if v < 0:
        raise ValueError("Сумма не может быть отрицательной")
    return v


@dp.message(StateFilter(Add.date), F.text, NOT_MENU)
async def add_day_text(msg: Message, state: FSMContext):
    try:
        day = parse_date(msg.text)
    except ValueError as e:
        await msg.answer(f"⚠️ {e}", reply_markup=kb([CANCEL]))
        return
    await state.update_data(day=day.isoformat())
    await ask_amount(msg, msg.from_user.id, state)


def preview(uid, model, day, nums):
    nums = nums + [0.0] * (3 - len(nums))
    rates = apply_rates(model, db.rates_for(uid, model, day), *nums)
    return service.shift_text(db.names(uid), model, day, *nums, rates)


async def ask_amount(msg: Message, uid: int, state: FSMContext, edit_msg=False):
    data = await state.get_data()
    model, nums, day = data["model"], data["nums"], date.fromisoformat(data["day"])
    steps = STEPS[model]
    if len(nums) == len(steps):
        await state.set_state(Add.confirm)
        text = "Проверь и сохрани:\n\n" + preview(uid, model, day, nums)
        markup = kb([("✅ Сохранить", "save")], [("✏️ Заново", "redo"), CANCEL])
    else:
        await state.set_state(Add.amount)
        done = "".join(f"\n✔️ {steps[i]}: {money(v)}" for i, v in enumerate(nums))
        text = (f"➕ <b>{db.names(uid)[model]}</b> · {d(day)}{done}\n\n"
                f"Сколько <b>{steps[len(nums)]}</b>? Напиши сумму в $ или нажми 0")
        markup = kb([("0", "zero")], [("⬅️ Назад", "back"), CANCEL])
    if edit_msg:
        await edit(msg, text, markup)
    else:
        await msg.answer(text, reply_markup=markup)


@dp.message(StateFilter(Add.amount), F.text, NOT_MENU)
async def add_amount_text(msg: Message, state: FSMContext):
    try:
        v = parse_amount(msg.text)
    except ValueError as e:
        await msg.answer(f"⚠️ {e}")
        return
    data = await state.get_data()
    await state.update_data(nums=data["nums"] + [v])
    await ask_amount(msg, msg.from_user.id, state)


@dp.callback_query(StateFilter(Add.amount), F.data == "zero")
async def add_zero(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(nums=data["nums"] + [0.0])
    await ask_amount(call.message, call.from_user.id, state, edit_msg=True)
    await call.answer()


@dp.callback_query(StateFilter(Add.amount), F.data == "back")
async def add_back(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data["nums"]:
        await state.update_data(nums=data["nums"][:-1])
        await ask_amount(call.message, call.from_user.id, state, edit_msg=True)
    else:
        await state.set_state(Add.date)
        await edit(call.message, f"➕ <b>{db.names(call.from_user.id)[data['model']]}</b> — за какой день смена?",
                   day_kb())
    await call.answer()


@dp.callback_query(StateFilter(Add.confirm), F.data == "redo")
async def add_redo(call: CallbackQuery, state: FSMContext):
    await state.update_data(nums=[])
    await ask_amount(call.message, call.from_user.id, state, edit_msg=True)
    await call.answer()


@dp.callback_query(StateFilter(Add.confirm), F.data == "save")
async def add_save(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid, model = call.from_user.id, data["model"]
    nums = data["nums"] + [0.0] * (3 - len(data["nums"]))
    day = date.fromisoformat(data["day"])
    db.add_shift(uid, model, day, *nums)
    text = "✅ Сохранено\n\n" + preview(uid, model, day, nums) + "\n\n" + goal_line(db, uid, today())
    await edit(call.message, text, app_button("📱 Открыть календарь"))
    await call.answer("Сохранено ✅")
    await refresh_excel(uid, call.message)


# ================= цель =================
class Goal(StatesGroup):
    amount = State()


async def ask_goal(msg: Message, key: str):
    suggest = db.last_goal(msg.chat.id) or DEFAULT_GOAL
    await msg.answer(f"🎯 Какая цель на {MONTHS[int(key[5:]) - 1]}? Сколько хочешь заработать за месяц?",
                     reply_markup=kb([(money(suggest), f"g:set:{key}:{suggest:g}"),
                                      ("Другая сумма", f"g:other:{key}")]))


@dp.message(Command("goal"))
async def cmd_goal(msg: Message, state: FSMContext):
    await state.clear()
    await ask_goal(msg, month_key(today()))


@dp.callback_query(F.data.startswith("g:"))
async def cb_goal(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    key = parts[2] if len(parts) > 2 else month_key(today())
    if parts[1] == "set":
        db.set_goal(call.from_user.id, key, float(parts[3]))
        await edit(call.message, f"🎯 Цель на {MONTHS[int(key[5:]) - 1]}: <b>{money(float(parts[3]))}</b>\n\n"
                   + goal_line(db, call.from_user.id, today()))
        await refresh_excel(call.from_user.id)
    elif parts[1] == "other":
        await state.set_state(Goal.amount)
        await state.update_data(key=key)
        await edit(call.message, "Напиши сумму цели в $, например 1800", kb([CANCEL]))
    await call.answer()


@dp.message(StateFilter(Goal.amount), F.text, NOT_MENU)
async def goal_text(msg: Message, state: FSMContext):
    try:
        v = parse_amount(msg.text)
        if v <= 0:
            raise ValueError("Цель должна быть больше нуля")
    except ValueError as e:
        await msg.answer(f"⚠️ {e}")
        return
    key = (await state.get_data())["key"]
    await state.clear()
    db.set_goal(msg.from_user.id, key, v)
    await msg.answer(f"🎯 Цель на {MONTHS[int(key[5:]) - 1]}: <b>{money(v)}</b>\n\n"
                     + goal_line(db, msg.from_user.id, today()))
    await refresh_excel(msg.from_user.id)


# ================= выплаты =================
def payouts_view(uid):
    names = db.names(uid)
    p = service.payouts(db, uid, today())
    lines = ["💸 <b>Выплаты</b>", ""]
    if p["waiting"]:
        lines.append(f"⏳ <b>Ждём: {money(p['owed'])}</b>")
        for i in p["waiting"]:
            lines.append(f"  {names[i['model']]} {d(date.fromisoformat(i['start']))}–{d(date.fromisoformat(i['end']))}:"
                         f" {money(i['pay'])} · ждём {i['wait_days']} дн.")
        lines.append("\nНажми на период, когда деньги пришли 👇")
    else:
        lines.append("✅ Все закрытые периоды выплачены")
    if p["avg_delay"] is not None:
        lines.append(f"\nВ среднем овнер платит через {p['avg_delay']:g} дн. после конца периода")
    rows = [[(f"✅ {names[i['model']]} {d(date.fromisoformat(i['start']))}–{d(date.fromisoformat(i['end']))}"
              f" · {money(i['pay'])}", f"pay:{i['model']}:{i['start']}")] for i in p["waiting"]]
    rows.append([("🔄 Обновить", "pays")])
    return "\n".join(lines), kb(*rows)


@dp.message(F.text == B_PAY)
@dp.message(Command("pay"))
async def pay_menu(msg: Message, state: FSMContext):
    await state.clear()
    text, markup = payouts_view(msg.from_user.id)
    await msg.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "pays")
async def cb_pays(call: CallbackQuery):
    await edit(call.message, *payouts_view(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data.startswith("pay:"))
async def cb_pay(call: CallbackQuery):
    _, model, start = call.data.split(":")
    db.mark_paid(call.from_user.id, model, date.fromisoformat(start), today())
    await call.answer("Отмечено как выплачено 💸")
    await edit(call.message, *payouts_view(call.from_user.id))
    await refresh_excel(call.from_user.id)


# ================= таблица =================
@dp.message(F.text == B_XLS)
@dp.message(Command("table"))
async def table(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    await Actions().send_excel(uid)
    await refresh_excel(uid)
    if uid in excel_pending:
        await msg.answer("⚠️ Файл на ПК открыт в Excel — обновлю его, когда закроешь.")


# ================= быстрый ввод строкой =================
@dp.message(StateFilter(None), F.text)
async def quick(msg: Message, state: FSMContext):
    tokens = msg.text.strip().split()
    if not tokens or tokens[0] not in ("1", "2"):
        await msg.answer("Выбери действие в меню 👇", reply_markup=menu(msg.from_user.id))
        return
    model = M1 if tokens[0] == "1" else M2
    day, nums = today(), []
    try:
        for i, t in enumerate(tokens[1:], 1):
            is_last = i == len(tokens) - 1 and i > 1       # дата — только последним словом
            if is_last and (t.count(".") == 2 or (t.count(".") == 1 and _looks_like_date(t))):
                day = parse_date(t)
            else:
                nums.append(parse_amount(t))
    except ValueError as e:
        await msg.answer(f"⚠️ {e}")
        return
    need = len(STEPS[model])
    if not 1 <= len(nums) <= need:
        await msg.answer("⚠️ Для первой модели до трёх сумм (обычные, сабки, бандлы), для второй — одна")
        return
    nums += [0.0] * (need - len(nums))
    await state.set_state(Add.confirm)
    await state.update_data(model=model, day=day.isoformat(), nums=nums)
    await msg.answer("Проверь и сохрани:\n\n" + preview(msg.from_user.id, model, day, nums),
                     reply_markup=kb([("✅ Сохранить", "save")], [("✏️ Заново", "redo"), CANCEL]))


def _looks_like_date(t: str) -> bool:
    """'11.11' — дата, '99.5' или '153.60' — сумма."""
    a, b = t.split(".")
    return a.isdigit() and b.isdigit() and len(a) <= 2 and len(b) == 2 and 1 <= int(a) <= 31 and 1 <= int(b) <= 12


@dp.callback_query()
async def cb_stale(call: CallbackQuery):
    await call.answer("Эта кнопка уже неактуальна")


# ================= фоновые задачи =================
def targets():
    return sorted(ALLOWED) if ALLOWED else db.users()


async def daily(bot: Bot, t: date):
    for uid in targets():
        names = db.names(uid)
        paid = db.paid_map(uid)
        for m in MODELS:
            start, end = period_for(m, t, -1)
            key = f"notified:{uid}:{m}:{start.isoformat()}"
            if db.meta_get(key):
                continue
            db.meta_set(key, "1")
            s = service.summarize(db.shifts(uid, m, start, end), db.adjustments(uid, m, start, end))
            if s["pay"] > 0 and (m, start) not in paid:
                await bot.send_message(uid, (
                    f"🔔 Период <b>{names[m]}</b> {d(start)}–{d(end)} закрыт\n"
                    f"Смен: {s['shifts']} · к выплате: <b>{money(s['pay'])}</b>\n\nКогда деньги придут — отметь 👇"),
                    reply_markup=kb([("✅ Уже выплатили", f"pay:{m}:{start.isoformat()}")], [("💸 Все выплаты", "pays")]))
        if t.weekday() == 0:
            late = [i for i in service.payouts(db, uid, t)["waiting"] if i["wait_days"] >= 7]
            if late:
                await bot.send_message(uid, (
                    f"⏳ Неделю+ ждёшь выплаты на <b>{money(sum(i['pay'] for i in late))}</b>:\n" + "\n".join(
                        f"  {names[i['model']]} {d(date.fromisoformat(i['start']))}–{d(date.fromisoformat(i['end']))}:"
                        f" {money(i['pay'])} · {i['wait_days']} дн." for i in late)),
                    reply_markup=kb([("💸 Открыть выплаты", "pays")]))
        if not db.get_goal(uid, month_key(t)):
            mk = f"goalasked:{uid}:{month_key(t)}"
            if not db.meta_get(mk):
                db.meta_set(mk, "1")
                await ask_goal(await bot.send_message(uid, f"🗓 Начался {MONTHS[t.month - 1]}!"), month_key(t))
    bdir = os.path.join(BASE, "backups")
    os.makedirs(bdir, exist_ok=True)
    db.backup(os.path.join(bdir, f"salary_{t.isoformat()}.db"))
    for old in sorted(os.listdir(bdir))[:-14]:
        try:
            os.remove(os.path.join(bdir, old))
        except OSError:
            pass


async def scheduler(bot: Bot, tunnel):
    while True:
        await asyncio.sleep(60)
        try:
            for uid in list(excel_pending):
                await refresh_excel(uid)
            if tunnel and not tunnel.alive():        # туннель упал — поднимаем заново
                logging.warning("tunnel died, restarting")
                await start_app(bot, tunnel)
            now = datetime.now(TZ)
            if now.hour >= NOTIFY_HOUR and db.meta_get("daily") != now.date().isoformat():
                db.meta_set("daily", now.date().isoformat())
                await daily(bot, now.date())
        except Exception:
            logging.exception("scheduler error")


async def start_app(bot: Bot, tunnel):
    url = await tunnel.start()
    if not url or not WEBAPP_URL:
        state_app["url"] = None
        return
    state_app["url"] = f"{WEBAPP_URL}/?api={quote(url, safe='')}"
    for uid in targets():
        try:
            await bot.set_chat_menu_button(chat_id=uid, menu_button=MenuButtonWebApp(
                text="Приложение", web_app=WebAppInfo(url=state_app["url"])))
        except Exception:
            logging.exception("menu button")


async def main():
    logging.basicConfig(level=logging.INFO)
    from aiohttp import web
    from tunnel import Tunnel

    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    bot_ref["bot"] = bot
    app = make_aiohttp_app(db, TOKEN, ALLOWED, today, uah_rate, Actions())
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", API_PORT).start()

    tunnel = Tunnel(BASE, API_PORT)
    await start_app(bot, tunnel)
    print("App URL:", state_app["url"] or "нет (приложение недоступно, бот работает)")
    for uid in targets():
        await refresh_excel(uid)
    asyncio.create_task(scheduler(bot, tunnel))
    try:
        await dp.start_polling(bot)
    finally:
        tunnel.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
