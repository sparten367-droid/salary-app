"use strict";
/* Mini App «Зарплата». Данные — в боте на ПК, сюда приходят через API (?api=...). */

const tg = window.Telegram && window.Telegram.WebApp;
const API = new URLSearchParams(location.search).get("api");
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

let S = null;                 // снимок данных от сервера
let view = "home";
let calMonth = null;          // "YYYY-MM"
const prefs = loadPrefs();

/* ================= утилиты ================= */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function money(x, sign = false) {
  const v = Math.round((x || 0) * 100) / 100;
  const s = Math.abs(v).toLocaleString("ru-RU", { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 }).replace(/ /g, " ");
  return (v < 0 ? "−" : sign && v > 0 ? "+" : "") + "$" + s;
}
function uah(x) {
  if (!prefs.uah || !S || !S.uah) return "";
  return "≈ " + Math.round(x * S.uah).toLocaleString("ru-RU").replace(/ /g, " ") + " ₴";
}
const pD = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const iso = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
const dm = (s) => s.slice(8, 10) + "." + s.slice(5, 7);
const mKey = (s) => s.slice(0, 7);
function shiftMonth(key, n) { const [y, m] = key.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return iso(d).slice(0, 7); }
const monthTitle = (key) => { const [y, m] = key.split("-").map(Number); return MONTHS[m - 1][0].toUpperCase() + MONTHS[m - 1].slice(1) + " " + y; };
const daysIn = (key) => { const [y, m] = key.split("-").map(Number); return new Date(y, m, 0).getDate(); };
const name = (m) => (S && S.names[m]) || m;
const sum = (arr, f) => arr.reduce((a, x) => a + (f ? f(x) : x), 0);
function loadPrefs() { try { return JSON.parse(localStorage.getItem("prefs") || "{}"); } catch { return {}; } }
function savePrefs() { try { localStorage.setItem("prefs", JSON.stringify(prefs)); } catch { /* нет хранилища */ } }
function haptic(type = "light") { try { tg && tg.HapticFeedback && (type === "ok" ? tg.HapticFeedback.notificationOccurred("success") : tg.HapticFeedback.impactOccurred(type)); } catch { /* */ } }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}

/* ================= API ================= */
async function api(path, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 20000);
  const res = await fetch(API.replace(/\/$/, "") + path, {
    signal: ctrl.signal,
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Init-Data": (tg && tg.initData) || "", "ngrok-skip-browser-warning": "1" },
    body: body ? JSON.stringify(body) : undefined,
  }).catch((e) => { throw new TypeError(e.name === "AbortError" ? "timeout" : e.message); }).finally(() => clearTimeout(timer));
  let data = {};
  try { data = await res.json(); } catch { /* */ }
  if (!res.ok) throw new Error(data.error || `Ошибка ${res.status}`);
  return data;
}
async function act(path, body, okMsg) {
  try {
    const data = await api(path, body);
    if (data.shifts) S = data;
    if (okMsg) { toast(okMsg); haptic("ok"); }
    render();
    return data;
  } catch (e) { toast("⚠️ " + e.message); haptic("heavy"); throw e; }
}

/* ================= расчёты на клиенте ================= */
function ratesFor(model, day) {
  let r = { ...S.rates_default[model] };
  for (const h of S.rates_history) if (h.model === model && h.valid_from <= day) r = { ...r, ...h };
  return r;
}
function calcShift(model, day, regular, subs, bundles) {
  const r = ratesFor(model, day);
  if (model === "m2") return { r_regular: r.regular, r_subs: 0, r_bundles: 0, bonus: false, pay: regular * r.regular / 100, rates: r };
  const total = regular + subs + bundles;
  const bonus = !!r.bonus_threshold && total >= r.bonus_threshold;
  const rr = r.regular + (bonus ? r.bonus_add : 0);
  return { r_regular: rr, r_subs: r.subs, r_bundles: r.bundles, bonus, rates: r, total,
    pay: (regular * rr + subs * r.subs + bundles * r.bundles) / 100 };
}
const shiftsOn = (day) => S.shifts.filter((s) => s.day === day);
const adjOn = (day) => S.adjustments.filter((a) => a.day === day);
function monthData(key) {
  return S.months.find((m) => m.month === key) || { month: key, total: 0, goal: S.goals[key] || null, m1: { pay: 0, sales: 0, shifts: 0, bonus_shifts: 0, adj: 0 }, m2: { pay: 0, sales: 0, shifts: 0, bonus_shifts: 0, adj: 0 } };
}

/* ================= каркас ================= */
function setTop(html) { $("#top").innerHTML = html; }
function destroyCharts() { /* SVG-графики пересоздаются при рендере */ }
function go(v) {
  view = v;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  window.scrollTo(0, 0);
  render();
}
function render() {
  if (!S) return;
  destroyCharts();
  ({ home: renderHome, calendar: renderCalendar, stats: renderStats, payouts: renderPayouts, more: renderMore })[view]();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { haptic(); go(b.dataset.view); }));

/* ================= Главная ================= */
function ringSVG(frac, size = 116, stroke = 11) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac));
  const col = frac >= 1 ? "var(--good)" : "var(--btn)";
  return `<svg width="${size}" height="${size}"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--bg2)" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${col}" stroke-width="${stroke}" stroke-linecap="round"
    stroke-dasharray="${c * f} ${c}"/></svg>`;
}
function renderHome() {
  const t = S.today, key = mKey(t), f = S.forecast, md = monthData(key), prev = monthData(shiftMonth(key, -1));
  const d = pD(t);
  setTop(`<h1>Привет 👋</h1><div class="sub">${d.getDate()} ${MONTHS_GEN[d.getMonth()]}, ${WD[(d.getDay() + 6) % 7].toLowerCase()}</div>`);
  const todayPay = sum(shiftsOn(t), (s) => s.pay);
  const diff = prev.total ? Math.round((md.total - prev.total) / prev.total * 100) : null;
  // для честного сравнения — прошлый месяц на ту же дату
  const prevSameDay = sum(S.shifts.filter((s) => mKey(s.day) === shiftMonth(key, -1) && Number(s.day.slice(8)) <= d.getDate()), (s) => s.pay);
  let goalHtml;
  if (f.goal) {
    const frac = f.earned / f.goal;
    const onTrack = f.projected >= f.goal;
    goalHtml = `<div class="card tap" id="goal-card"><div class="goal">
      <div class="ring">${ringSVG(frac)}<div class="c"><div><div class="pct">${Math.round(frac * 100)}%</div><div class="muted">цели</div></div></div></div>
      <div class="info">
        <div class="big">${money(f.earned)}</div><div class="muted">из ${money(f.goal)} за ${MONTHS[d.getMonth()]}</div>
        ${f.left > 0 ? `<div class="muted">Осталось <b class="num" style="color:var(--text)">${money(f.left)}</b> · ~${money(f.need_per_day)}/день</div>` : `<div class="tag ok">🏆 Цель выполнена</div>`}
        <div><span class="tag ${onTrack ? "ok" : "warn"}">${onTrack ? "📈" : "⚠️"} Прогноз ${money(f.projected)}</span></div>
      </div></div></div>`;
  } else {
    goalHtml = `<div class="card"><div class="row"><div class="grow"><div class="big">${money(f.earned)}</div><div class="muted">за ${MONTHS[d.getMonth()]} · прогноз ${money(f.projected)}</div></div>
      <button class="btn small" id="set-goal">🎯 Задать цель</button></div></div>`;
  }
  const cur = S.periods.filter((p) => p.status === "current");
  const owed = S.payouts.owed, waiting = S.payouts.waiting;
  const recent = [...S.shifts].sort((a, b) => b.day.localeCompare(a.day) || b.id - a.id).slice(0, 5);
  $("#view").innerHTML = `
    ${goalHtml}
    <div class="quick">
      <button class="btn m1" data-add="m1">＋ ${esc(name("m1"))}</button>
      <button class="btn m2" data-add="m2">＋ ${esc(name("m2"))}</button>
    </div>
    <div class="grid2">
      <div class="card"><h3>Сегодня</h3><div class="mid">${money(todayPay)}</div><div class="uah">${uah(todayPay)}</div></div>
      <div class="card"><h3>Месяц</h3><div class="mid">${money(md.total)}</div>
        <div class="muted">${prevSameDay ? `<span class="${md.total >= prevSameDay ? "up" : "down"}">${md.total >= prevSameDay ? "▲" : "▼"} ${Math.abs(Math.round((md.total - prevSameDay) / prevSameDay * 100))}%</span> к ${d.getDate()} ${MONTHS_GEN[(d.getMonth() + 11) % 12]}` : (diff !== null ? "" : "&nbsp;")}</div></div>
    </div>
    <div class="card tap" data-go="payouts"><div class="row">
      <div class="grow"><h3 style="margin-bottom:4px">Ждём от овнера</h3>
        <div class="mid">${money(owed)}</div><div class="uah">${uah(owed)}</div></div>
      <div class="right">${waiting.length ? `<span class="tag ${Math.max(...waiting.map((w) => w.wait_days)) >= 7 ? "bad" : "warn"}">⏳ ${waiting.length} ${plural(waiting.length, "период", "периода", "периодов")}</span>
        <div class="muted" style="margin-top:6px">дольше всех — ${Math.max(...waiting.map((w) => w.wait_days))} дн.</div>` : `<span class="tag ok">✅ всё выплачено</span>`}</div>
    </div></div>
    <div class="section-title">Текущие периоды</div>
    <div class="list">${cur.map((p) => `<div class="item" data-go="calendar">
      <span class="dot ${p.model}"></span><div class="grow"><div class="t">${esc(name(p.model))}</div><div class="s">${dm(p.start)}–${dm(p.end)} · ${p.shifts} ${plural(p.shifts, "смена", "смены", "смен")}</div></div>
      <div class="right"><div class="mid">${money(p.pay)}</div><div class="uah">${uah(p.pay)}</div></div></div>`).join("") || `<div class="item"><div class="s">Смен пока нет</div></div>`}</div>
    <div class="section-title">Последние смены</div>
    <div class="list">${recent.map(shiftItem).join("") || `<div class="item"><div class="s">Пока пусто — добавь первую смену</div></div>`}</div>`;
  bindCommon();
  const gc = $("#goal-card"); if (gc) gc.onclick = () => goalSheet(key);
  const sg = $("#set-goal"); if (sg) sg.onclick = () => goalSheet(key);
}
function plural(n, a, b, c) { n = Math.abs(n) % 100; const n1 = n % 10; return n > 10 && n < 20 ? c : n1 > 1 && n1 < 5 ? b : n1 === 1 ? a : c; }
function shiftItem(s) {
  const parts = s.model === "m1"
    ? [s.regular && `обычные ${money(s.regular)}`, s.subs && `сабки ${money(s.subs)}`, s.bundles && `бандлы ${money(s.bundles)}`].filter(Boolean).join(" · ")
    : `продажи ${money(s.regular)}`;
  return `<div class="item" data-shift="${s.id}"><span class="dot ${s.model}"></span>
    <div class="grow"><div class="t">${esc(name(s.model))} · ${dm(s.day)} ${s.bonus ? '<span class="tag bonus">🔥 бонус</span>' : ""}</div>
    <div class="s">${parts || "без продаж"}${s.note ? " · " + esc(s.note) : ""}</div></div>
    <div class="right mid">${money(s.pay)}</div></div>`;
}
function bindCommon(root = document) {
  root.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => shiftSheet({ model: b.dataset.add, day: b.dataset.day || S.today })));
  root.querySelectorAll("[data-shift]").forEach((b) => (b.onclick = () => shiftSheet(S.shifts.find((s) => s.id === Number(b.dataset.shift)))));
  root.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go)));
}

/* ================= Календарь ================= */
function renderCalendar() {
  calMonth = calMonth || mKey(S.today);
  const key = calMonth, md = monthData(key);
  setTop(`<div class="month-nav"><button class="icon-btn" id="pm">‹</button><h1>${monthTitle(key)}</h1>
    <button class="icon-btn" id="nm" ${key >= mKey(S.today) ? "disabled" : ""}>›</button></div>`);
  const [y, m] = key.split("-").map(Number);
  const first = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const ends = new Set(S.periods.map((p) => p.end));
  let cells = "";
  for (let i = 0; i < first; i++) cells += `<div class="day out"></div>`;
  for (let dd = 1; dd <= daysIn(key); dd++) {
    const day = `${key}-${String(dd).padStart(2, "0")}`;
    const sh = shiftsOn(day), adj = adjOn(day);
    const pay = sum(sh, (s) => s.pay) + sum(adj, (a) => a.amount);
    const models = [...new Set(sh.map((s) => s.model))];
    const cls = ["day", sh.length ? "work" : "", sh.some((s) => s.bonus) ? "bonus" : "", day === S.today ? "today" : "", ends.has(day) ? "pend" : ""].join(" ");
    cells += `<div class="${cls}" data-day="${day}"><span class="n">${dd}</span>
      ${pay ? `<span class="p">${pay >= 1000 ? "$" + (pay / 1000).toFixed(1) + "k" : "$" + Math.round(pay)}</span>` : ""}
      <span class="dots">${models.map((mm) => `<span class="dot ${mm}"></span>`).join("")}${adj.length ? "<span>✦</span>" : ""}</span></div>`;
  }
  const mPeriods = S.periods.filter((p) => mKey(p.end) === key || mKey(p.start) === key).filter((p) => p.pay > 0 || p.status === "current");
  const statusTag = (p) => p.status === "paid" ? `<span class="tag ok">✓ ${p.imported ? "" : dm(p.paid_on)}</span>` : p.status === "current" ? `<span class="tag">идёт</span>` : `<span class="tag ${p.wait_days >= 7 ? "bad" : "warn"}">⏳ ${p.wait_days} дн.</span>`;
  $("#view").innerHTML = `
    <div class="grid2">
      <div class="card"><div class="row"><span class="dot m1"></span><span class="muted">${esc(name("m1"))}</span></div><div class="mid">${money(md.m1.pay)}</div><div class="muted">${md.m1.shifts} ${plural(md.m1.shifts, "смена", "смены", "смен")}${md.m1.bonus_shifts ? ` · 🔥${md.m1.bonus_shifts}` : ""}</div></div>
      <div class="card"><div class="row"><span class="dot m2"></span><span class="muted">${esc(name("m2"))}</span></div><div class="mid">${money(md.m2.pay)}</div><div class="muted">${md.m2.shifts} ${plural(md.m2.shifts, "смена", "смены", "смен")}</div></div>
    </div>
    <div class="cal"><div class="wd">${WD.map((w) => `<div>${w}</div>`).join("")}</div><div class="days">${cells}</div></div>
    <div class="legend"><span><i style="background:color-mix(in srgb,var(--bonus) 40%,transparent)"></i>бонус 300+</span>
      <span><i style="background:var(--good);width:6px;height:6px;border-radius:2px"></i>конец периода</span><span>✦ доп. начисление</span></div>
    <div class="card"><div class="row"><div class="grow"><h3 style="margin:0">Итого за месяц</h3></div><div class="big">${money(md.total)}</div></div>
      ${md.goal ? `<div class="muted right">цель ${money(md.goal)} · ${Math.round(md.total / md.goal * 100)}%</div>` : ""}<div class="uah right">${uah(md.total)}</div></div>
    <div class="section-title">Периоды выплат</div>
    <div class="list">${mPeriods.map((p) => `<div class="item" data-period="${p.model}|${p.start}"><span class="dot ${p.model}"></span>
      <div class="grow"><div class="t">${esc(name(p.model))} · ${dm(p.start)}–${dm(p.end)}</div><div class="s">${p.shifts} ${plural(p.shifts, "смена", "смены", "смен")} · продажи ${money(p.sales)}</div></div>
      <div class="right"><div class="mid">${money(p.pay)}</div>${statusTag(p)}</div></div>`).join("") || `<div class="item"><div class="s">Нет периодов</div></div>`}</div>`;
  $("#pm").onclick = () => { calMonth = shiftMonth(key, -1); render(); };
  $("#nm").onclick = () => { calMonth = shiftMonth(key, 1); render(); };
  document.querySelectorAll("[data-day]").forEach((c) => (c.onclick = () => { haptic(); daySheet(c.dataset.day); }));
  document.querySelectorAll("[data-period]").forEach((c) => (c.onclick = () => { const [mm, st] = c.dataset.period.split("|"); periodSheet(S.periods.find((p) => p.model === mm && p.start === st)); }));
}

/* ================= Статистика ================= */
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
/* Столбчатый график (стопкой) на SVG: подсказка по нажатию/наведению, легенда при 2+ рядах. */
function barChart(el, labels, sets, opts = {}) {
  const W = Math.max(280, el.clientWidth), H = el.clientHeight || 220;
  const padL = 40, padR = 6, padT = 8, padB = 22;
  const totals = labels.map((_, i) => sum(sets, (s) => s.data[i] || 0));
  const goal = opts.goal || [];
  let max = Math.max(1, ...totals, ...goal.filter((g) => g));
  const step = niceStep(max / 4); max = Math.ceil(max / step) * step;
  const iw = W - padL - padR, ih = H - padT - padB, bw = iw / labels.length;
  const barW = Math.max(3, Math.min(34, bw * 0.62));
  const y = (v) => padT + ih - (v / max) * ih;
  let g = "";
  for (let v = 0; v <= max + 1e-9; v += step) {
    g += `<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1"/>
      <text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end" class="ax">${v >= 1000 ? "$" + (v / 1000) + "k" : "$" + v}</text>`;
  }
  const every = Math.ceil(labels.length / Math.floor(iw / 26));
  labels.forEach((lb, i) => {
    const cx = padL + bw * i + bw / 2;
    let base = 0;
    sets.forEach((s, si) => {
      const v = s.data[i] || 0;
      if (v <= 0) return;
      const top = base + v, isTop = sets.slice(si + 1).every((t) => !(t.data[i] > 0));
      const y1 = y(top), h = y(base) - y1 - (base > 0 ? 2 : 0);
      if (h > 0.5) g += isTop
        ? `<path d="${roundTop(cx - barW / 2, y1, barW, h, Math.min(4, barW / 2, h))}" fill="${s.color}"/>`
        : `<rect x="${cx - barW / 2}" y="${y1}" width="${barW}" height="${h}" fill="${s.color}"/>`;
      base = top;
    });
    if (i % every === 0) g += `<text x="${cx}" y="${H - 6}" text-anchor="middle" class="ax">${lb}</text>`;
    g += `<rect class="hit" data-i="${i}" x="${padL + bw * i}" y="${padT}" width="${bw}" height="${ih}" fill="transparent"/>`;
  });
  if (goal.some((v) => v)) {
    const pts = goal.map((v, i) => (v ? `${padL + bw * i + bw / 2},${y(v)}` : null)).filter(Boolean);
    g += `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--hint)" stroke-width="2" stroke-dasharray="5 4"/>`;
    goal.forEach((v, i) => { if (v) g += `<circle cx="${padL + bw * i + bw / 2}" cy="${y(v)}" r="3" fill="var(--hint)"/>`; });
  }
  const legend = sets.length > 1 || goal.some((v) => v)
    ? `<div class="legend" style="padding:0 0 6px">${sets.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}${goal.some((v) => v) ? `<span><i style="background:none;border-top:2px dashed var(--hint);height:0;width:14px;border-radius:0"></i>цель</span>` : ""}</div>` : "";
  el.innerHTML = legend + `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img">${g}</svg><div class="tip" hidden></div>`;
  const tip = el.querySelector(".tip");
  const show = (i, x) => {
    el.querySelectorAll(".hit").forEach((h) => h.classList.toggle("on", Number(h.dataset.i) === i));
    const rows = sets.filter((s) => s.data[i]).map((s) => `<div class="row"><span class="dot" style="background:${s.color}"></span><span class="grow">${esc(s.label)}</span><b class="num">${money(s.data[i])}</b></div>`).join("");
    tip.innerHTML = `<div class="muted">${opts.tipLabel ? opts.tipLabel(i) : labels[i]}</div>${rows || '<div class="muted">нет данных</div>'}
      ${sets.length > 1 && totals[i] ? `<div class="row" style="border-top:1px solid var(--line);margin-top:4px;padding-top:4px"><span class="grow">Итого</span><b class="num">${money(totals[i])}</b></div>` : ""}
      ${goal[i] ? `<div class="muted">цель ${money(goal[i])}</div>` : ""}`;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = Math.min(Math.max(0, x - tw / 2), el.clientWidth - tw) + "px";
  };
  el.querySelectorAll(".hit").forEach((h) => {
    const i = Number(h.dataset.i), x = padL + bw * i + bw / 2;
    h.addEventListener("click", (e) => { e.stopPropagation(); haptic(); show(i, x); });
    h.addEventListener("mouseenter", () => show(i, x));
  });
  el.addEventListener("mouseleave", () => { tip.hidden = true; el.querySelectorAll(".hit").forEach((h) => h.classList.remove("on")); });
}
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1))), n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
function roundTop(x, y, w, h, r) {
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}
function renderStats() {
  setTop(`<h1>Статистика</h1><div class="sub">Всего заработано ${money(S.records.total_earned)} за ${S.records.days_worked} ${plural(S.records.days_worked, "рабочий день", "рабочих дня", "рабочих дней")}</div>`);
  const months = S.months.slice(-12);
  const key = mKey(S.today), md = monthData(key);
  const cur = S.shifts.filter((s) => mKey(s.day) === key);
  const best = S.records.best_day;
  const bestMonth = S.months.reduce((a, m) => (!a || m.total > a.total ? m : a), null);
  const avgShift = (() => { const days = new Set(S.shifts.map((s) => s.day)).size; return days ? S.records.total_earned / days : 0; })();
  const share1 = md.total ? md.m1.pay / md.total : 0;
  $("#view").innerHTML = `
    <div class="grid2">
      <div class="card"><h3>Лучший день</h3><div class="mid">${best ? money(best.pay) : "—"}</div><div class="muted">${best ? dm(best.day) + "." + best.day.slice(2, 4) : ""}</div></div>
      <div class="card"><h3>Лучший месяц</h3><div class="mid">${bestMonth ? money(bestMonth.total) : "—"}</div><div class="muted">${bestMonth ? monthTitle(bestMonth.month).toLowerCase() : ""}</div></div>
      <div class="card"><h3>Средний день</h3><div class="mid">${money(avgShift)}</div><div class="muted">когда есть смены</div></div>
      <div class="card"><h3>Доля в ${MONTHS[pD(S.today).getMonth()].slice(0, 3)}.</h3><div class="mid">${Math.round(share1 * 100)}% / ${Math.round((1 - share1) * 100)}%</div>
        <div class="bar-wrap"><div style="width:${share1 * 100}%;background:var(--s1)"></div><div style="flex:1;background:var(--s2)"></div></div></div>
    </div>
    <div class="card"><h3>По месяцам</h3><div class="chart tall" id="ch-months"></div></div>
    <div class="card"><h3>${monthTitle(key)} по дням</h3><div class="chart" id="ch-days"></div></div>
    <div class="card"><h3>Средний заработок по дням недели</h3><div class="chart" id="ch-wd"></div>
      <div class="muted" style="margin-top:6px">${(() => { const b = [...S.weekdays].sort((a, c) => c.avg - a.avg)[0]; return b && b.avg ? `Самый прибыльный день — ${b.weekday.toLowerCase()} (${money(b.avg)} в среднем)` : ""; })()}</div></div>
    <div class="card"><h3>Таблица по месяцам</h3><table class="tbl"><thead><tr><th>Месяц</th><th>${esc(name("m1"))}</th><th>${esc(name("m2"))}</th><th>Итого</th><th>Цель</th></tr></thead>
      <tbody>${[...S.months].reverse().map((m) => `<tr><td>${MONTHS[Number(m.month.slice(5)) - 1].slice(0, 3)} ${m.month.slice(2, 4)}</td><td>${money(m.m1.pay)}</td><td>${money(m.m2.pay)}</td><td><b>${money(m.total)}</b></td>
      <td>${m.goal ? `<span class="${m.total >= m.goal ? "up" : ""}">${Math.round(m.total / m.goal * 100)}%</span>` : "—"}</td></tr>`).join("")}</tbody></table></div>`;
  const s1 = "var(--s1)", s2 = "var(--s2)";
  const ml = months.map((m) => MONTHS[Number(m.month.slice(5)) - 1].slice(0, 3));
  barChart($("#ch-months"), ml, [
    { label: name("m1"), color: s1, data: months.map((m) => m.m1.pay) },
    { label: name("m2"), color: s2, data: months.map((m) => m.m2.pay) },
  ], { goal: months.map((m) => m.goal), tipLabel: (i) => monthTitle(months[i].month) });
  const n = daysIn(key), days = Array.from({ length: n }, (_, i) => `${key}-${String(i + 1).padStart(2, "0")}`);
  barChart($("#ch-days"), days.map((d) => Number(d.slice(8))), [
    { label: name("m1"), color: s1, data: days.map((d) => sum(cur.filter((s) => s.day === d && s.model === "m1"), (s) => s.pay)) },
    { label: name("m2"), color: s2, data: days.map((d) => sum(cur.filter((s) => s.day === d && s.model === "m2"), (s) => s.pay)) },
  ], { tipLabel: (i) => `${i + 1} ${MONTHS_GEN[Number(key.slice(5)) - 1]}` });
  barChart($("#ch-wd"), S.weekdays.map((w) => w.weekday), [
    { label: "В среднем за день", color: "var(--btn)", data: S.weekdays.map((w) => w.avg) },
  ], { tipLabel: (i) => `${S.weekdays[i].weekday} · ${S.weekdays[i].days} ${plural(S.weekdays[i].days, "день", "дня", "дней")}` });
}

/* ================= Выплаты ================= */
function renderPayouts() {
  const P = S.payouts;
  setTop(`<h1>Выплаты</h1><div class="sub">${P.avg_delay !== null ? `Овнер в среднем платит через ${P.avg_delay} дн. после конца периода` : "Отмечай выплаты, когда деньги приходят"}</div>`);
  const waitTag = (p) => `<span class="tag ${p.wait_days >= 7 ? "bad" : "warn"}">⏳ ждём ${p.wait_days} дн.</span>`;
  $("#view").innerHTML = `
    <div class="card"><h3>Ждём от овнера</h3><div class="big">${money(P.owed)}</div><div class="uah">${uah(P.owed)}</div></div>
    ${P.waiting.length ? `<div class="section-title">Не выплачено</div><div class="list">${P.waiting.map((p) => `
      <div class="item" data-period="${p.model}|${p.start}"><span class="dot ${p.model}"></span>
      <div class="grow"><div class="t">${esc(name(p.model))} · ${dm(p.start)}–${dm(p.end)}</div><div class="s">${p.shifts} ${plural(p.shifts, "смена", "смены", "смен")}</div></div>
      <div class="right"><div class="mid">${money(p.pay)}</div>${waitTag(p)}</div></div>`).join("")}</div>` : ""}
    ${P.current.length ? `<div class="section-title">Идут сейчас</div><div class="list">${P.current.map((p) => `
      <div class="item" data-period="${p.model}|${p.start}"><span class="dot ${p.model}"></span>
      <div class="grow"><div class="t">${esc(name(p.model))} · ${dm(p.start)}–${dm(p.end)}</div><div class="s">закроется через ${Math.round((pD(p.end) - pD(S.today)) / 864e5) + 1} дн.</div></div>
      <div class="right mid">${money(p.pay)}</div></div>`).join("")}</div>` : ""}
    <div class="section-title">История выплат</div>
    <div class="list">${P.paid.filter((p) => !p.imported).slice(0, 30).map((p) => `<div class="item" data-period="${p.model}|${p.start}"><span class="dot ${p.model}"></span>
      <div class="grow"><div class="t">${esc(name(p.model))} · ${dm(p.start)}–${dm(p.end)}</div><div class="s">${p.imported ? "выплачено (из старой таблицы)" : `пришло ${dm(p.paid_on)} · через ${p.wait_days} дн.`}</div></div>
      <div class="right mid">${money(p.pay)}</div></div>`).join("")}
      ${(() => { const imp = P.paid.filter((p) => p.imported); return imp.length ? `<div class="item" style="cursor:default"><span>🗂</span>
        <div class="grow"><div class="t">Из старой таблицы</div><div class="s">${imp.length} ${plural(imp.length, "период", "периода", "периодов")} · ${dm(imp[imp.length - 1].start)}.${imp[imp.length - 1].start.slice(2, 4)}–${dm(imp[0].end)}</div></div>
        <div class="right mid">${money(sum(imp, (p) => p.pay))}</div></div>` : ""; })()}
      ${P.paid.length ? "" : `<div class="item"><div class="s">Пока нет отмеченных выплат</div></div>`}</div>`;
  document.querySelectorAll("[data-period]").forEach((c) => (c.onclick = () => { const [mm, st] = c.dataset.period.split("|"); periodSheet(S.periods.find((p) => p.model === mm && p.start === st)); }));
}

/* ================= Ещё / настройки ================= */
function renderMore() {
  setTop(`<h1>Ещё</h1>`);
  const key = mKey(S.today), next = shiftMonth(key, 1);
  const r1 = S.rates.m1, r2 = S.rates.m2;
  const adjs = [...S.adjustments].sort((a, b) => b.day.localeCompare(a.day)).slice(0, 20);
  $("#view").innerHTML = `
    <div class="section-title">Цели</div>
    <div class="list">
      <div class="item" id="g-cur"><span>🎯</span><div class="grow"><div class="t">${monthTitle(key)}</div></div><div class="mid">${S.goals[key] ? money(S.goals[key]) : "задать"}</div></div>
      <div class="item" id="g-next"><span>🗓</span><div class="grow"><div class="t">${monthTitle(next)}</div></div><div class="mid">${S.goals[next] ? money(S.goals[next]) : "задать"}</div></div>
    </div>
    <div class="section-title">Ставки</div>
    <div class="list">
      <div class="item" data-rates="m1"><span class="dot m1"></span><div class="grow"><div class="t">${esc(name("m1"))}</div>
        <div class="s">обычные ${r1.regular}% · сабки ${r1.subs}% · бандлы ${r1.bundles}% · от ${money(r1.bonus_threshold)} за смену +${r1.bonus_add}%</div></div><span class="muted">›</span></div>
      <div class="item" data-rates="m2"><span class="dot m2"></span><div class="grow"><div class="t">${esc(name("m2"))}</div><div class="s">${r2.regular}% от продаж</div></div><span class="muted">›</span></div>
    </div>
    ${S.rates_history.length ? `<div class="muted" style="margin:-4px 8px 8px">Изменения ставок: ${S.rates_history.map((h) => `${esc(name(h.model))} с ${dm(h.valid_from)}.${h.valid_from.slice(2, 4)}`).join(", ")}</div>` : ""}
    <div class="section-title">Доп. начисления</div>
    <div class="list">
      <div class="item" id="adj-add"><span>➕</span><div class="grow"><div class="t" style="color:var(--link)">Добавить бонус или штраф</div><div class="s">Разовые суммы вне смен: бонус за тотал, оплата сценария…</div></div></div>
      ${adjs.map((a) => `<div class="item" data-adj="${a.id}"><span class="dot ${a.model}"></span><div class="grow"><div class="t">${esc(a.note || "Доп. начисление")}</div><div class="s">${esc(name(a.model))} · ${dm(a.day)}.${a.day.slice(2, 4)}</div></div>
        <div class="mid ${a.amount >= 0 ? "up" : "down"}">${money(a.amount, true)}</div></div>`).join("")}
    </div>
    <div class="section-title">Прочее</div>
    <div class="list">
      <div class="item" id="names"><span>✏️</span><div class="grow"><div class="t">Названия моделей</div><div class="s">${esc(name("m1"))}, ${esc(name("m2"))}</div></div><span class="muted">›</span></div>
      <div class="item" id="uah-toggle"><span>₴</span><div class="grow"><div class="t">Показывать в гривнах</div><div class="s">${S.uah ? `курс НБУ: ${S.uah} ₴ за $1` : "курс сейчас недоступен"}</div></div>
        <input type="checkbox" ${prefs.uah ? "checked" : ""} style="width:22px;height:22px;pointer-events:none"></div>
      <div class="item" id="excel"><span>📁</span><div class="grow"><div class="t">Excel-таблица в чат</div><div class="s">Все смены, выплаты и месяцы</div></div></div>
    </div>
    <div class="muted" style="text-align:center;margin:20px 0 8px">Данные хранятся у тебя на ПК · v1.0</div>`;
  $("#g-cur").onclick = () => goalSheet(key);
  $("#g-next").onclick = () => goalSheet(next);
  document.querySelectorAll("[data-rates]").forEach((b) => (b.onclick = () => ratesSheet(b.dataset.rates)));
  $("#adj-add").onclick = () => adjSheet();
  document.querySelectorAll("[data-adj]").forEach((b) => (b.onclick = () => adjViewSheet(S.adjustments.find((a) => a.id === Number(b.dataset.adj)))));
  $("#names").onclick = namesSheet;
  $("#uah-toggle").onclick = () => { prefs.uah = !prefs.uah; savePrefs(); render(); };
  $("#excel").onclick = () => act("/api/excel", {}, "Отправил таблицу в чат 📁");
}

/* ================= шторки ================= */
function openSheet(html, onOpen) {
  const sh = $("#sheet"), bd = $("#sheet-backdrop");
  sh.innerHTML = `<div class="grip"></div>${html}`;
  sh.hidden = false; bd.hidden = false;
  document.body.style.overflow = "hidden";
  bd.onclick = closeSheet;
  if (tg && tg.BackButton) { tg.BackButton.show(); tg.BackButton.onClick(closeSheet); }
  onOpen && onOpen(sh);
}
function closeSheet() {
  $("#sheet").hidden = true; $("#sheet-backdrop").hidden = true;
  document.body.style.overflow = "";
  if (tg && tg.BackButton) { tg.BackButton.offClick(closeSheet); tg.BackButton.hide(); }
}
const numVal = (el) => { const v = parseFloat(String(el.value).replace(",", ".")); return isNaN(v) ? 0 : v; };

function shiftSheet(s) {
  const editing = !!s.id;
  let model = s.model;
  const html = () => `
    <h2>${editing ? "Смена" : "Новая смена"}</h2>
    <div class="seg" id="seg">${["m1", "m2"].map((m) => `<button data-m="${m}" class="${m === model ? "on" : ""}"><span class="dot ${m}"></span>${esc(name(m))}</button>`).join("")}</div>
    <div class="field"><label>Дата смены</label><input type="date" id="f-day" value="${s.day}" max="${S.today}"></div>
    ${model === "m1" ? `<div class="fields3">
      <div class="field"><label>Обычные, $</label><input inputmode="decimal" id="f-reg" placeholder="0" value="${s.regular || ""}"></div>
      <div class="field"><label>Сабки, $</label><input inputmode="decimal" id="f-subs" placeholder="0" value="${s.subs || ""}"></div>
      <div class="field"><label>Бандлы, $</label><input inputmode="decimal" id="f-bund" placeholder="0" value="${s.bundles || ""}"></div></div>`
    : `<div class="field"><label>Продажи, $</label><input inputmode="decimal" id="f-reg" placeholder="0" value="${s.regular || ""}"></div>`}
    <div class="preview" id="pv"></div>
    <div class="field"><label>Заметка</label><input id="f-note" maxlength="300" placeholder="например: мало онлайна" value="${esc(s.note || "")}"></div>
    <div class="actions"><button class="btn" id="f-save">${editing ? "Сохранить" : "Добавить"}</button></div>
    ${editing ? `<button class="btn danger" id="f-del" style="margin-top:6px">Удалить смену</button>` : ""}`;
  const mount = (sh) => {
    sh.innerHTML = `<div class="grip"></div>` + html();
    const upd = () => {
      const day = $("#f-day", sh).value || S.today;
      const reg = numVal($("#f-reg", sh)), subs = model === "m1" ? numVal($("#f-subs", sh)) : 0, bund = model === "m1" ? numVal($("#f-bund", sh)) : 0;
      const c = calcShift(model, day, reg, subs, bund);
      let lines;
      if (model === "m1") {
        lines = `<div>Всего продаж: <b>${money(c.total)}</b> ${c.bonus ? '<span class="tag bonus">🔥 бонус +' + c.rates.bonus_add + "%</span>" : ""}</div>
          <div class="muted">обычные ${c.r_regular}% · сабки ${c.r_subs}% · бандлы ${c.r_bundles}%</div>
          ${!c.bonus && c.rates.bonus_threshold && c.total > 0 ? `<div class="muted">До бонуса: ${money(c.rates.bonus_threshold - c.total)}</div>` : ""}`;
      } else lines = `<div class="muted">ставка ${c.r_regular}%</div>`;
      $("#pv", sh).innerHTML = lines + `<div class="total">${money(c.pay)}</div><div class="uah">${uah(c.pay)}</div>`;
    };
    sh.querySelectorAll("input").forEach((i) => i.addEventListener("input", upd));
    sh.querySelectorAll("#seg button").forEach((b) => (b.onclick = () => {
      s = { ...s, day: $("#f-day", sh).value, regular: numVal($("#f-reg", sh)), note: $("#f-note", sh).value };
      model = b.dataset.m; mount(sh);
    }));
    $("#f-save", sh).onclick = async (e) => {
      e.target.disabled = true;
      const body = { id: s.id, model, day: $("#f-day", sh).value, regular: numVal($("#f-reg", sh)), note: $("#f-note", sh).value,
        subs: model === "m1" ? numVal($("#f-subs", sh)) : 0, bundles: model === "m1" ? numVal($("#f-bund", sh)) : 0 };
      if (!body.day) { toast("Укажи дату"); e.target.disabled = false; return; }
      try { await act("/api/shift", body, editing ? "Сохранено ✓" : "Смена добавлена ✓"); closeSheet(); } catch { e.target.disabled = false; }
    };
    const del = $("#f-del", sh);
    if (del) del.onclick = () => confirmAsk("Удалить эту смену?", async () => { await act("/api/shift/delete", { id: s.id }, "Смена удалена"); closeSheet(); });
    upd();
    if (!editing) setTimeout(() => { const f = $("#f-reg", sh); f && f.focus(); }, 300);
  };
  openSheet("", mount);
}

function daySheet(day) {
  const sh = shiftsOn(day), adj = adjOn(day);
  const total = sum(sh, (s) => s.pay) + sum(adj, (a) => a.amount);
  const d = pD(day);
  openSheet(`
    <h2>${d.getDate()} ${MONTHS_GEN[d.getMonth()]}, ${WD[(d.getDay() + 6) % 7].toLowerCase()}</h2>
    ${sh.length || adj.length ? `<div class="list" style="background:var(--bg2)">${sh.map(shiftItem).join("")}
      ${adj.map((a) => `<div class="item" data-adj="${a.id}"><span>✦</span><div class="grow"><div class="t">${esc(a.note || "Доп. начисление")}</div><div class="s">${esc(name(a.model))}</div></div><div class="mid">${money(a.amount, true)}</div></div>`).join("")}</div>
      <div class="row" style="margin:4px 4px 14px"><div class="grow muted">За день</div><div class="big">${money(total)}</div></div>` : `<div class="empty" style="padding:20px">В этот день смен нет</div>`}
    ${day <= S.today ? `<div class="quick"><button class="btn m1" data-add="m1" data-day="${day}">＋ ${esc(name("m1"))}</button><button class="btn m2" data-add="m2" data-day="${day}">＋ ${esc(name("m2"))}</button></div>
    <button class="btn secondary" id="add-adj">✦ Доп. начисление</button>` : ""}`, (root) => {
    bindCommon(root);
    root.querySelectorAll("[data-adj]").forEach((b) => (b.onclick = () => adjViewSheet(S.adjustments.find((a) => a.id === Number(b.dataset.adj)))));
    const aa = $("#add-adj", root); if (aa) aa.onclick = () => adjSheet(day);
  });
}

function periodSheet(p) {
  if (!p) return;
  const shifts = S.shifts.filter((s) => s.model === p.model && s.day >= p.start && s.day <= p.end);
  const adjs = S.adjustments.filter((a) => a.model === p.model && a.day >= p.start && a.day <= p.end);
  const status = p.status === "paid" ? `<span class="tag ok">✓ выплачено${p.imported ? "" : ` ${dm(p.paid_on)} (через ${p.wait_days} дн.)`}</span>`
    : p.status === "current" ? `<span class="tag">идёт</span>` : `<span class="tag ${p.wait_days >= 7 ? "bad" : "warn"}">⏳ ждём ${p.wait_days} дн.</span>`;
  openSheet(`
    <h2><span class="dot ${p.model}"></span> ${esc(name(p.model))} · ${dm(p.start)}–${dm(p.end)}</h2>
    <div class="row" style="margin-bottom:12px"><div class="grow">${status}</div><div class="right"><div class="big">${money(p.pay)}</div><div class="uah">${uah(p.pay)}</div></div></div>
    <div class="list" style="background:var(--bg2);max-height:40vh;overflow:auto">${shifts.map(shiftItem).join("")}
      ${adjs.map((a) => `<div class="item"><span>✦</span><div class="grow"><div class="t">${esc(a.note || "Доп. начисление")}</div><div class="s">${dm(a.day)}</div></div><div class="mid">${money(a.amount, true)}</div></div>`).join("")
      || (shifts.length ? "" : `<div class="item"><div class="s">Смен нет</div></div>`)}</div>
    <div class="muted" style="margin:-2px 4px 14px">Продажи ${money(p.sales)} · ${p.shifts} ${plural(p.shifts, "смена", "смены", "смен")}</div>
    ${p.status === "waiting" ? `<div class="field"><label>Когда пришли деньги</label><input type="date" id="paid-on" value="${S.today}" max="${S.today}" min="${p.end}"></div>
      <button class="btn" id="mark">✅ Деньги пришли</button>` : ""}
    ${p.status === "paid" ? `<button class="btn secondary" id="unmark">↩️ Отменить отметку о выплате</button>` : ""}
    <div class="actions" style="margin-top:8px"><button class="btn secondary" id="report">📨 Отчёт в чат</button><button class="btn secondary" id="copy">📋 Копировать</button></div>
    <div class="muted" style="margin-top:8px">Отчёт для сверки с овнером: все смены и итог. Пришлю его в чат — останется только переслать.</div>`, (root) => {
    bindCommon(root);
    const mk = $("#mark", root);
    if (mk) mk.onclick = async () => { await act("/api/payout", { model: p.model, start: p.start, paid_on: $("#paid-on", root).value || S.today }, "Отмечено 💸"); closeSheet(); };
    const um = $("#unmark", root);
    if (um) um.onclick = async () => { await act("/api/payout", { model: p.model, start: p.start, paid_on: null }, "Отметка снята"); closeSheet(); };
    $("#report", root).onclick = () => act("/api/report", { model: p.model, start: p.start }, "Отчёт в чате — перешли его овнеру");
    $("#copy", root).onclick = async () => {
      try {
        const r = await api("/api/report?dry=1", { model: p.model, start: p.start, dry: true });
        await navigator.clipboard.writeText(r.text); toast("Скопировано 📋");
      } catch (e) { toast("Не удалось скопировать — используй «Отчёт в чат»"); }
    };
  });
}

function goalSheet(key) {
  const suggest = S.goals[key] || Object.entries(S.goals).sort().reverse().map((x) => x[1])[0] || 1500;
  const prev = monthData(shiftMonth(key, -1)).total;
  openSheet(`<h2>🎯 Цель на ${MONTHS[Number(key.slice(5)) - 1]}</h2>
    <div class="field"><label>Сколько хочу заработать за месяц, $</label><input inputmode="decimal" id="g-amt" value="${suggest}"></div>
    ${prev ? `<div class="muted" style="margin:-4px 4px 14px">В прошлом месяце: ${money(prev)}</div>` : ""}
    <button class="btn" id="g-save">Сохранить</button>`, (root) => {
    $("#g-save", root).onclick = async () => {
      const v = numVal($("#g-amt", root));
      if (v <= 0) return toast("Цель должна быть больше нуля");
      await act("/api/goal", { month: key, amount: v }, "Цель сохранена 🎯"); closeSheet();
    };
  });
}

function ratesSheet(model) {
  const r = S.rates[model];
  const f = (id, label, v, suf = "%") => `<div class="field"><label>${label}${suf ? ", " + suf : ""}</label><input inputmode="decimal" id="${id}" value="${v}"></div>`;
  openSheet(`<h2><span class="dot ${model}"></span> Ставки · ${esc(name(model))}</h2>
    ${model === "m1" ? `<div class="fields3">${f("r-reg", "Обычные", r.regular)}${f("r-subs", "Сабки", r.subs)}${f("r-bund", "Бандлы", r.bundles)}</div>
      <div class="fields3" style="grid-template-columns:1fr 1fr">${f("r-th", "Бонус от суммы смены", r.bonus_threshold, "$")}${f("r-add", "Бонус к обычным", r.bonus_add, "+%")}</div>`
    : f("r-reg", "Процент от продаж", r.regular)}
    <div class="field"><label>Действуют с</label><input type="date" id="r-from" value="${S.today}"></div>
    <label class="check"><input type="checkbox" id="r-recalc"><span>Пересчитать уже внесённые смены с этой даты<br><span class="muted">Если не отмечать — старые смены останутся по старым ставкам</span></span></label>
    <button class="btn" id="r-save">Сохранить ставки</button>
    ${S.rates_history.filter((h) => h.model === model).length ? `<div class="section-title">История</div><div class="list" style="background:var(--bg2)">${S.rates_history.filter((h) => h.model === model).reverse().map((h) => `
      <div class="item"><div class="grow"><div class="t">с ${dm(h.valid_from)}.${h.valid_from.slice(0, 4)}</div>
      <div class="s">${model === "m1" ? `${h.regular}% / ${h.subs}% / ${h.bundles}% · бонус от ${money(h.bonus_threshold)} +${h.bonus_add}%` : `${h.regular}%`}</div></div>
      <button class="btn ghost" data-delrate="${h.valid_from}">удалить</button></div>`).join("")}</div>` : ""}`, (root) => {
    $("#r-save", root).onclick = async () => {
      const data = model === "m1"
        ? { regular: numVal($("#r-reg", root)), subs: numVal($("#r-subs", root)), bundles: numVal($("#r-bund", root)), bonus_threshold: numVal($("#r-th", root)), bonus_add: numVal($("#r-add", root)) }
        : { regular: numVal($("#r-reg", root)) };
      await act("/api/rates", { model, valid_from: $("#r-from", root).value || S.today, data, recalc: $("#r-recalc", root).checked }, "Ставки сохранены");
      closeSheet();
    };
    root.querySelectorAll("[data-delrate]").forEach((b) => (b.onclick = () => confirmAsk("Удалить это изменение ставок? Уже внесённые смены не пересчитаются.", async () => {
      await act("/api/rates/delete", { model, valid_from: b.dataset.delrate }, "Удалено"); closeSheet();
    })));
  });
}

function adjSheet(day) {
  let model = "m1";
  openSheet(`<h2>✦ Доп. начисление</h2>
    <div class="seg" id="seg">${["m1", "m2"].map((m) => `<button data-m="${m}" class="${m === model ? "on" : ""}"><span class="dot ${m}"></span>${esc(name(m))}</button>`).join("")}</div>
    <div class="seg" id="sign"><button data-s="1" class="on">➕ Бонус</button><button data-s="-1">➖ Штраф</button></div>
    <div class="field"><label>Сумма к выплате, $</label><input inputmode="decimal" id="a-amt" placeholder="50"></div>
    <div class="field"><label>Дата</label><input type="date" id="a-day" value="${day || S.today}" max="${S.today}"></div>
    <div class="field"><label>За что</label><input id="a-note" maxlength="300" placeholder="бонус за лучший тотал"></div>
    <div class="muted" style="margin:-4px 4px 14px">Попадёт в выплату за период, в который входит дата</div>
    <button class="btn" id="a-save">Добавить</button>`, (root) => {
    let sign = 1;
    root.querySelectorAll("#seg button").forEach((b) => (b.onclick = () => { model = b.dataset.m; root.querySelectorAll("#seg button").forEach((x) => x.classList.toggle("on", x === b)); }));
    root.querySelectorAll("#sign button").forEach((b) => (b.onclick = () => { sign = Number(b.dataset.s); root.querySelectorAll("#sign button").forEach((x) => x.classList.toggle("on", x === b)); }));
    $("#a-save", root).onclick = async () => {
      const v = numVal($("#a-amt", root));
      if (!v) return toast("Укажи сумму");
      await act("/api/adjustment", { model, day: $("#a-day", root).value || S.today, amount: sign * Math.abs(v), note: $("#a-note", root).value }, "Добавлено ✦");
      closeSheet();
    };
  });
}

function adjViewSheet(a) {
  if (!a) return;
  openSheet(`<h2>✦ ${esc(a.note || "Доп. начисление")}</h2>
    <div class="row" style="margin-bottom:14px"><div class="grow muted">${esc(name(a.model))} · ${dm(a.day)}.${a.day.slice(0, 4)}</div><div class="big">${money(a.amount, true)}</div></div>
    <button class="btn danger" id="a-del">Удалить</button>`, (root) => {
    $("#a-del", root).onclick = () => confirmAsk("Удалить доп. начисление?", async () => { await act("/api/adjustment/delete", { id: a.id }, "Удалено"); closeSheet(); });
  });
}

function namesSheet() {
  openSheet(`<h2>✏️ Названия моделей</h2>
    <div class="field"><label><span class="dot m1"></span> Первая (обычные / сабки / бандлы)</label><input id="n1" maxlength="30" value="${esc(name("m1"))}"></div>
    <div class="field"><label><span class="dot m2"></span> Вторая (одна ставка)</label><input id="n2" maxlength="30" value="${esc(name("m2"))}"></div>
    <button class="btn" id="n-save">Сохранить</button>`, (root) => {
    $("#n-save", root).onclick = async () => { await act("/api/names", { m1: $("#n1", root).value, m2: $("#n2", root).value }, "Сохранено"); closeSheet(); };
  });
}

function confirmAsk(text, fn) {
  if (tg && tg.showConfirm && tg.platform !== "unknown") tg.showConfirm(text, (ok) => ok && fn());
  else if (window.confirm(text)) fn();
}

/* ================= запуск ================= */
function fatal(icon, title, text) {
  setTop("");
  $("#tabs").hidden = true;
  $("#view").innerHTML = `<div class="fatal"><div class="ic">${icon}</div><h2>${title}</h2><p class="muted">${text}</p>
    ${API ? `<button class="btn" onclick="location.reload()" style="max-width:240px">Попробовать снова</button>` : ""}</div>`;
}
async function start() {
  if (tg) {
    tg.ready(); tg.expand();
    document.documentElement.dataset.theme = tg.colorScheme === "dark" ? "dark" : "light";
    tg.onEvent && tg.onEvent("themeChanged", () => { document.documentElement.dataset.theme = tg.colorScheme === "dark" ? "dark" : "light"; render(); });
    try { tg.setHeaderColor("secondary_bg_color"); tg.setBackgroundColor("secondary_bg_color"); } catch { /* */ }
  } else if (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) {
    document.documentElement.dataset.theme = "dark";
  }
  if (!API) return fatal("📱", "Открой через бота", "Это приложение работает внутри Telegram: нажми кнопку «Приложение» в своём боте.");
  try {
    S = await api("/api/all");
    $("#tabs").hidden = false;
    render();
  } catch (e) {
    const offline = e instanceof TypeError;
    fatal(offline ? "💤" : "🔒", offline ? "Бот не отвечает" : "Нет доступа",
      offline ? "Похоже, компьютер с ботом выключен или бот перезапускался. Открой приложение заново из бота." : esc(e.message));
  }
}
start();
