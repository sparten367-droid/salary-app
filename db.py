"""Хранение данных в SQLite."""
import json
import sqlite3
from datetime import date

from calc import DEFAULT_NAMES, DEFAULT_RATES, MODELS, apply_rates

SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    model     TEXT    NOT NULL,
    day       TEXT    NOT NULL,            -- YYYY-MM-DD, дата начала смены
    regular   REAL    NOT NULL DEFAULT 0,  -- у модели 2 — все продажи
    subs      REAL    NOT NULL DEFAULT 0,
    bundles   REAL    NOT NULL DEFAULT 0,
    r_regular REAL    NOT NULL DEFAULT 0,  -- применённые ставки, %
    r_subs    REAL    NOT NULL DEFAULT 0,
    r_bundles REAL    NOT NULL DEFAULT 0,
    bonus     INTEGER NOT NULL DEFAULT 0,
    note      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS shifts_day ON shifts(user_id, model, day);
CREATE TABLE IF NOT EXISTS adjustments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    model   TEXT    NOT NULL,
    day     TEXT    NOT NULL,
    amount  REAL    NOT NULL,              -- + бонус, − штраф (в $ к выплате)
    note    TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payouts (
    user_id      INTEGER NOT NULL,
    model        TEXT    NOT NULL,
    period_start TEXT    NOT NULL,
    paid_on      TEXT    NOT NULL,
    PRIMARY KEY (user_id, model, period_start)
);
CREATE TABLE IF NOT EXISTS goals (
    user_id INTEGER NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS rates (
    user_id INTEGER NOT NULL, model TEXT NOT NULL, valid_from TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (user_id, model, valid_from)
);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SHIFT_COLS = ["r_regular", "r_subs", "r_bundles", "bonus", "note"]


class DB:
    def __init__(self, path: str = "salary.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate_old()
        self.conn.executescript(SCHEMA)

    def _migrate_old(self):
        """Старая схема: без ставок в сменах."""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(shifts)")]
        if not cols or "r_regular" in cols:
            return
        for c, typ in [("r_regular", "REAL NOT NULL DEFAULT 0"), ("r_subs", "REAL NOT NULL DEFAULT 0"),
                       ("r_bundles", "REAL NOT NULL DEFAULT 0"), ("bonus", "INTEGER NOT NULL DEFAULT 0"),
                       ("note", "TEXT NOT NULL DEFAULT ''")]:
            self.conn.execute(f"ALTER TABLE shifts ADD COLUMN {c} {typ}")
        for r in self.conn.execute("SELECT * FROM shifts WHERE model IN ('m1', 'm2')").fetchall():
            a = apply_rates(r["model"], DEFAULT_RATES[r["model"]], r["regular"], r["subs"], r["bundles"])
            self.conn.execute("UPDATE shifts SET r_regular=?, r_subs=?, r_bundles=?, bonus=? WHERE id=?",
                              (a["r_regular"], a["r_subs"], a["r_bundles"], a["bonus"], r["id"]))
        self.conn.commit()

    def _exec(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # ---------- ставки и настройки ----------
    def rates_for(self, uid, model, day: date) -> dict:
        r = self.conn.execute(
            "SELECT data FROM rates WHERE user_id=? AND model=? AND valid_from<=? ORDER BY valid_from DESC LIMIT 1",
            (uid, model, day.isoformat())).fetchone()
        return {**DEFAULT_RATES[model], **json.loads(r[0])} if r else dict(DEFAULT_RATES[model])

    def rates_history(self, uid):
        return [{"model": r[0], "valid_from": r[1], **json.loads(r[2])} for r in self.conn.execute(
            "SELECT model, valid_from, data FROM rates WHERE user_id=? ORDER BY valid_from", (uid,))]

    def set_rates(self, uid, model, valid_from: date, data: dict):
        self._exec("INSERT OR REPLACE INTO rates VALUES (?,?,?,?)",
                   (uid, model, valid_from.isoformat(), json.dumps(data)))

    def delete_rates(self, uid, model, valid_from: str):
        self._exec("DELETE FROM rates WHERE user_id=? AND model=? AND valid_from=?", (uid, model, valid_from))

    def setting(self, uid, key, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE user_id=? AND key=?", (uid, key)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, uid, key, value):
        self._exec("INSERT OR REPLACE INTO settings VALUES (?,?,?)", (uid, key, json.dumps(value)))

    def names(self, uid) -> dict:
        return {**DEFAULT_NAMES, **(self.setting(uid, "names") or {})}

    # ---------- смены ----------
    def add_shift(self, uid, model, day: date, regular, subs=0.0, bundles=0.0, note="", rates=None) -> int:
        a = rates or apply_rates(model, self.rates_for(uid, model, day), regular, subs, bundles)
        return self._exec(
            "INSERT INTO shifts (user_id, model, day, regular, subs, bundles, r_regular, r_subs, r_bundles, bonus, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, model, day.isoformat(), regular, subs, bundles,
             a["r_regular"], a["r_subs"], a["r_bundles"], a["bonus"], note)).lastrowid

    def update_shift(self, uid, shift_id, model, day: date, regular, subs=0.0, bundles=0.0, note="") -> bool:
        a = apply_rates(model, self.rates_for(uid, model, day), regular, subs, bundles)
        return self._exec(
            "UPDATE shifts SET model=?, day=?, regular=?, subs=?, bundles=?, r_regular=?, r_subs=?, r_bundles=?,"
            " bonus=?, note=? WHERE id=? AND user_id=?",
            (model, day.isoformat(), regular, subs, bundles, a["r_regular"], a["r_subs"], a["r_bundles"],
             a["bonus"], note, shift_id, uid)).rowcount > 0

    def get_shift(self, uid, shift_id):
        return self.conn.execute("SELECT * FROM shifts WHERE id=? AND user_id=?", (shift_id, uid)).fetchone()

    def delete_shift(self, uid, shift_id) -> bool:
        return self._exec("DELETE FROM shifts WHERE id=? AND user_id=?", (shift_id, uid)).rowcount > 0

    def shifts(self, uid, model=None, start: date = None, end: date = None):
        sql, args = "SELECT * FROM shifts WHERE user_id=?", [uid]
        if model:
            sql += " AND model=?"; args.append(model)
        if start:
            sql += " AND day>=?"; args.append(start.isoformat())
        if end:
            sql += " AND day<=?"; args.append(end.isoformat())
        return self.conn.execute(sql + " ORDER BY day, id", args).fetchall()

    def first_day(self, uid, model):
        r = self.conn.execute(
            "SELECT MIN(d) FROM (SELECT MIN(day) d FROM shifts WHERE user_id=? AND model=?"
            " UNION SELECT MIN(day) FROM adjustments WHERE user_id=? AND model=?)",
            (uid, model, uid, model)).fetchone()
        return date.fromisoformat(r[0]) if r and r[0] else None

    def users(self):
        return [r[0] for r in self.conn.execute("SELECT DISTINCT user_id FROM shifts")]

    # ---------- доп. начисления ----------
    def add_adjustment(self, uid, model, day: date, amount, note="") -> int:
        return self._exec("INSERT INTO adjustments (user_id, model, day, amount, note) VALUES (?,?,?,?,?)",
                          (uid, model, day.isoformat(), amount, note)).lastrowid

    def delete_adjustment(self, uid, adj_id) -> bool:
        return self._exec("DELETE FROM adjustments WHERE id=? AND user_id=?", (adj_id, uid)).rowcount > 0

    def adjustments(self, uid, model=None, start: date = None, end: date = None):
        sql, args = "SELECT * FROM adjustments WHERE user_id=?", [uid]
        if model:
            sql += " AND model=?"; args.append(model)
        if start:
            sql += " AND day>=?"; args.append(start.isoformat())
        if end:
            sql += " AND day<=?"; args.append(end.isoformat())
        return self.conn.execute(sql + " ORDER BY day, id", args).fetchall()

    # ---------- выплаты ----------
    def mark_paid(self, uid, model, start: date, paid_on: date):
        self._exec("INSERT OR REPLACE INTO payouts VALUES (?,?,?,?)",
                   (uid, model, start.isoformat(), paid_on.isoformat()))

    def unmark_paid(self, uid, model, start: date):
        self._exec("DELETE FROM payouts WHERE user_id=? AND model=? AND period_start=?",
                   (uid, model, start.isoformat()))

    def paid_map(self, uid) -> dict:
        return {(r[0], date.fromisoformat(r[1])): date.fromisoformat(r[2]) for r in self.conn.execute(
            "SELECT model, period_start, paid_on FROM payouts WHERE user_id=?", (uid,))}

    # ---------- цели ----------
    def set_goal(self, uid, month: str, amount: float):
        self._exec("INSERT OR REPLACE INTO goals VALUES (?,?,?)", (uid, month, amount))

    def get_goal(self, uid, month: str):
        r = self.conn.execute("SELECT amount FROM goals WHERE user_id=? AND month=?", (uid, month)).fetchone()
        return r[0] if r else None

    def last_goal(self, uid):
        r = self.conn.execute("SELECT amount FROM goals WHERE user_id=? ORDER BY month DESC LIMIT 1",
                              (uid,)).fetchone()
        return r[0] if r else None

    def goals(self, uid) -> dict:
        return {r[0]: r[1] for r in self.conn.execute("SELECT month, amount FROM goals WHERE user_id=?", (uid,))}

    # ---------- служебное ----------
    def meta_get(self, key):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def meta_set(self, key, value):
        self._exec("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def backup(self, dest_path: str):
        dst = sqlite3.connect(dest_path)
        with dst:
            self.conn.backup(dst)
        dst.close()
