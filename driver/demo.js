/*
 * driver/demo.js — демонстрационное приложение водителя (18 сен 2026).
 *
 * Владелец: «сделай демо, чтобы можно было посмотреть, как выглядит приёмка,
 * заказ и работа с ним, и чтобы каждая функция раскрылась как надо; поставлю
 * его им на все телефоны».
 *
 * Страница /demo — это НАСТОЯЩИЙ driver/index.html. Подменены ровно три вещи:
 * телеграм (заглушка), api.js (этот файл) и boot() → demoBoot(), который
 * чинит камеру со сканером и зовёт тот же самый boot(). Всё остальное —
 * разметка, стили, логика экранов — боевое, слово в слово.
 *
 * Сервер здесь живёт в этой же вкладке: помнит, что нажали, и отвечает так же,
 * как отвечает настоящий. Поэтому смена правда открывается, заказ правда
 * приезжает, приёмка правда сканируется, расход правда уходит на согласование,
 * а смена правда не закрывается, пока дела не сделаны.
 *
 * Данные ненастоящие и никуда не уходят: ни одного запроса за пределы вкладки.
 * Состояние — своё на каждом телефоне (localStorage), «Начать заново» стирает.
 */
(function(){
'use strict';

const noop = function(){};
const LS = {
  get(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } },
  set(k, v){ try{ localStorage.setItem(k, v); }catch(e){} },
  del(k){ try{ localStorage.removeItem(k); }catch(e){} },
};

/* ═══ 1. Телеграм ═══════════════════════════════════════════════════════════
   Демо открывают двумя путями, и они разные.

   Из телеграма, кнопкой web_app, — там телеграм настоящий, и трогать его
   нечего: полный экран, своя шапка, отклик кнопок, подтверждение выхода и
   запрет свайпа вниз приложение получает само, как в бою. Подменяем ровно
   одно — геопозицию: у демо её спрашивать не за чем, а без ответа не
   открывается смена.

   Из браузера (ссылкой, с домашнего экрана) телеграма нет вовсе — тогда
   ставим заглушку целиком: она отвечает на всё, о чём приложение спрашивает. */
const DEMO_POINT = {latitude: 25.0721, longitude: 55.1394, horizontal_accuracy: 12};
const LM = {
  isInited: true, isLocationAvailable: true, isAccessRequested: true, isAccessGranted: true,
  init(cb){ cb && cb(); },
  getLocation(cb){ cb && cb(DEMO_POINT); },
};
// Настоящий телеграм узнаём по площадке: библиотека, загруженная вне
// телеграма, пишет туда 'unknown'.
const TGW = (window.Telegram || {}).WebApp;
const INSIDE = !!(TGW && TGW.platform && TGW.platform !== 'unknown');
if(INSIDE){
  try{ TGW.LocationManager = LM; }
  catch(e){ try{ Object.defineProperty(TGW, 'LocationManager', {value: LM, configurable: true}); }catch(e2){} }
}
if(!INSIDE) window.Telegram = {WebApp: new Proxy({
  initData: 'demo', initDataUnsafe: {user: {id: 0, first_name: 'Демо'}},
  version: '8.0', platform: 'ios', colorScheme: 'dark', themeParams: {},
  viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight, isExpanded: true,
  isVersionAtLeast(){ return true; },
  HapticFeedback: {impactOccurred: noop, notificationOccurred: noop, selectionChanged: noop},
  BackButton: {show: noop, hide: noop, onClick: noop, offClick: noop},
  MainButton: {show: noop, hide: noop, setText: noop, onClick: noop},
  LocationManager: LM,
  ready: noop, expand: noop, onEvent: noop, offEvent: noop, close: noop,
  showConfirm(t, cb){ cb && cb(window.confirm(t)); },
  showAlert(t, cb){ window.alert(t); cb && cb(); },
  openLink(u){ try{ window.open(u, '_blank'); }catch(e){} },
  openTelegramLink: noop, setHeaderColor: noop, setBackgroundColor: noop,
  enableClosingConfirmation: noop, disableVerticalSwipes: noop, requestFullscreen: noop,
}, {get(t, k){ return k in t ? t[k] : noop; }})};

/* ═══ 2. Товар и люди ══════════════════════════════════════════════════════ */
const P = {
  p1:  {id: 'p1',  name: 'Absolut 1 ltr',      cat: 'Водка', price: 100, buy: 62},
  p55: {id: 'p55', name: "Gordon's 1 ltr",     cat: 'Джин',  price: 100, buy: 55},
  p14: {id: 'p14', name: 'Jameson 1 ltr',      cat: 'Виски', price: 200, buy: 118},
  p12: {id: 'p12', name: 'Jack Daniels 1 ltr', cat: 'Виски', price: 200, buy: 120},
  p6:  {id: 'p6',  name: 'Beluga 0.7 ltr',     cat: 'Водка', price: 250, buy: 150},
  p31: {id: 'p31', name: 'Heineken 0.33 can',  cat: 'Пиво',  price: 100, buy: 62, pack: true},
};
const ME = {id: 'demo', name: 'Демо', district: 'jvc', district_code: 'B2',
            district_name: 'JVC', operator: 'Умар'};
const DISTR = [{id: 'alguses', code: 'B1', name: 'Алгусес'}, {id: 'jvc', code: 'B2', name: 'JVC'},
               {id: 'bbay', code: 'B3', name: 'Бизнес Бей'}, {id: 'tecom', code: 'B4', name: 'Теком'},
               {id: 'silicon', code: 'B5', name: 'Силикон Оазис'}];
const DCODE = {}, DNAME = {};
DISTR.forEach(function(d){ DCODE[d.id] = d.code; DNAME[d.id] = d.name; });

const KEY = 'ambar_demo_v1';
const now = () => Date.now();
const iso = ms => new Date(ms).toISOString().replace('Z', '');
const hhmm = ms => {
  const d = new Date(ms);
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
};
const day = () => {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
};
const num = v => Math.round(v * 100) / 100;

/* ═══ 3. Мир демо ═══════════════════════════════════════════════════════════
   Одна заявка на приёмку, одна на перемещение, два заказа и расходы. Числа
   подобраны так, чтобы всё влезало в одну «смену» за пять минут: заказ на
   наличные и заказ, оплаченный в приложении; приёмка из трёх позиций;
   перемещение из двух районов; обязательный ответ про заправку. */
function line(pid, n, unit){
  const p = P[pid];
  return {id: p.id, name: p.name, need: n, got: 0, left: n, qty_total: n, unit_n: unit || 1,
          unit_name: unit && unit > 1 ? 'коробку' : 'бутылку', units: n, price: 0};
}
function mvLine(pid, from, qty){
  return {from: from, from_code: DCODE[from], from_name: DNAME[from], id: pid, name: P[pid].name,
          unit: 1, qty: qty, got: 0, left: qty, done: false};
}
function fresh(){
  const t = now();
  return {
    v: 1, day: day(), born: t,
    sh: {opened: false, opened_at: 0, closed: false, closed_at: 0},
    ord: [], ev: {}, chat: {},
    sup: {
      supply_id: 'D260918-01', at: t - 40 * 60000, day: day(), district: 'jvc',
      driver: ME.name, claimed_at: t - 30 * 60000, started_at: 0, done_at: 0, noscan_at: 0,
      lines: [line('p1', 12), line('p55', 6), line('p14', 4)],
    },
    supTaken: {
      supply_id: 'D260918-02', at: t - 38 * 60000, day: day(), district: 'tecom',
      driver: 'Фарух', claimed_at: t - 20 * 60000, started_at: t - 12 * 60000, done_at: 0, noscan_at: 0,
      lines: [line('p12', 6), line('p6', 2)],
    },
    mv: {
      move_id: 'MV260918-01', at: t - 50 * 60000, day: day(), district: 'jvc', driver: '',
      claimed_at: 0, started_at: 0, done_at: 0,
      lines: [mvLine('p1', 'alguses', 3), mvLine('p55', 'alguses', 2), mvLine('p14', 'bbay', 4)],
    },
    give: {
      move_id: 'MV260918-01', at: t - 50 * 60000, day: day(), district: 'bbay', driver: 'Азиз',
      claimed_at: t - 10 * 60000, started_at: 0, done_at: 0,
      lines: [mvLine('p6', 'jvc', 2)],
    },
    exp: [], noexp: {}, asked: [], wo: [], seq: 0, scans: 0,
  };
}
let S = (function(){
  try{
    const raw = LS.get(KEY);
    const s = raw ? JSON.parse(raw) : null;
    if(s && s.v === 1 && s.day === day()) return s;
  }catch(e){}
  return fresh();
})();
function save(){ LS.set(KEY, JSON.stringify(S)); }
window.demoReset = function(){
  // Заодно забываем, что вступление уже показывали: заново демо чаще всего
  // начинают, чтобы дать телефон другому человеку.
  LS.del(KEY); LS.del(KEY + '_seen'); location.reload();
};

/* Часы демо. Мир двигается не таймерами, а на каждый запрос приложения: так
   он одинаков после перезагрузки страницы и не зависит от того, спал телефон
   или нет. */
function tick(){
  const sh = S.sh;
  if(!sh.opened || sh.closed) return;
  const sec = (now() - sh.opened_at) / 1000;
  if(sec > 4 && !S.ev.o1){ S.ev.o1 = 1; S.ord.push(order1()); }
  const o1d = S.ord.find(o => o.n === 1 && o.delivered_at);
  if(!S.ev.o2 && (o1d || sec > 150)){ S.ev.o2 = 1; S.ord.push(order2()); }
  // Оператор отвечает в разговоре через полминуты после того, как заказ приняли.
  const o1 = S.ord.find(o => o.n === 1);
  if(o1 && o1.ack_ms && !S.ev.chat && now() - o1.ack_ms > 30000){
    S.ev.chat = 1;
    const msg = {by: 'operator', name: 'Умар', text: 'Клиент предупреждён, домофон не работает — звоните на телефон', at: iso(now()), kind: ''};
    (S.chat[o1.order_id] = S.chat[o1.order_id] || []).push(msg);
    o1.chat_n = (o1.chat_n || 0) + 1; o1.chat_new = 1;
    o1.chat_last = {text: msg.text, at: msg.at, name: 'Умар'};
  }
  // Смену района закрывает оператор, и только после этого водитель закрывает
  // свою. В демо оператор «просыпается», когда все заказы доставлены.
  if(!S.ev.dayclose && S.ord.length >= 2 && S.ord.every(o => o.delivered_at)) S.ev.dayclose = now() + 20000;
  // Расход старший смотрит не мгновенно: полминуты «на согласовании» — это то,
  // что водитель видит в жизни, и в демо это видно тоже.
  S.exp.forEach(function(e){
    if(e.status === 'pending' && now() - e.at_ms > 30000) e.status = 'approved';
  });
  save();
}

function order1(){
  const t = now();
  return {
    n: 1, order_id: 'AMB' + String(100000 + Math.floor(Math.random() * 899999)),
    status: 'approved', address: 'JVC, Gardenia 2, кв. 1204', gmap_link: 'https://maps.google.com/?q=25.0601,55.2098',
    location: {lat: 25.0601, lon: 55.2098}, district: 'JVC', customer_name: 'Клиент', source: 'app', tip: 0,
    items: [{id: 'p1', name: P.p1.name, qty: 1, pcs: null, price: 100, line_total: 100, gift: false},
            {id: 'p55', name: P.p55.name, qty: 2, pcs: null, price: 100, line_total: 200, gift: false}],
    total: 300, comment: 'Домофон не работает, позвоните с улицы', payment_method: 'cash', prepaid: false,
    timestamp: iso(t - 6 * 60000), confirmed_at: iso(t), delivered_at: '', deliver_by: hhmm(t + 25 * 60000),
    eta: 25, driver_ack_at: '', ack_ms: 0, driver_req: null, chat_n: 0, chat_new: 0, chat_at: '',
    chat_last: null, settle: null, owed: 0, pay_fx: null,
  };
}
function order2(){
  const t = now();
  return {
    n: 2, order_id: 'AMB' + String(100000 + Math.floor(Math.random() * 899999)),
    status: 'approved', address: 'JVC, Diamond Views 4, вилла 12', gmap_link: 'https://maps.google.com/?q=25.0563,55.2101',
    location: {lat: 25.0563, lon: 55.2101}, district: 'JVC', customer_name: 'Клиент', source: 'app', tip: 0,
    items: [{id: 'p14', name: P.p14.name, qty: 1, pcs: null, price: 200, line_total: 200, gift: false}],
    total: 200, comment: 'Позвонить за пять минут, встретит у шлагбаума',
    payment_method: 'card', prepaid: true, paid: true,
    timestamp: iso(t - 3 * 60000), confirmed_at: iso(t), delivered_at: '', deliver_by: hhmm(t + 35 * 60000),
    eta: 35, driver_ack_at: '', ack_ms: 0, driver_req: null, chat_n: 0, chat_new: 0, chat_at: '',
    chat_last: null, settle: null, owed: 0, pay_fx: null,
  };
}

/* ═══ 4. Виды, как их отдаёт боевой сервер ══════════════════════════════════ */
function supView(t){
  const lines = t.lines.map(function(l){
    return {...l, left: Math.max(0, l.need - l.got)};
  });
  const need = lines.reduce((a, l) => a + l.need, 0);
  const got = lines.reduce((a, l) => a + l.got, 0);
  return {
    supply_id: t.supply_id, at: iso(t.at), day: t.day, district: t.district,
    district_code: DCODE[t.district], district_name: DNAME[t.district],
    driver: t.driver, mine: t.driver === ME.name, extra: false, base: '',
    claimed_at: t.claimed_at ? iso(t.claimed_at) : '', started_at: t.started_at ? iso(t.started_at) : '',
    done_at: t.done_at ? iso(t.done_at) : '', locked: !!t.started_at, lock_at: '', erev: 0,
    noscan_at: t.noscan_at ? iso(t.noscan_at) : '', noscan_by: t.noscan_at ? ME.name : '',
    cancelled_at: '', cancelled_by: '', note: '', gaps: [], stale: false,
    need: need, got: got, left: Math.max(0, need - got), positions: lines.length, lines: lines,
    hold: {who: '', kind: '', live: false, mine: false}, prices_ok: true, cost: 0,
  };
}
function mvView(t, mine){
  const lines = t.lines.map(function(l){
    return {...l, left: num(Math.max(0, l.qty - l.got)), done: l.got >= l.qty};
  });
  const need = num(lines.reduce((a, l) => a + l.qty, 0));
  const got = num(lines.reduce((a, l) => a + l.got, 0));
  const src = {};
  lines.forEach(function(l){
    const s = src[l.from] || (src[l.from] = {district: l.from, code: l.from_code, name: l.from_name,
                                             qty: 0, got: 0, lines: []});
    s.qty = num(s.qty + l.qty); s.got = num(s.got + l.got); s.lines.push(l);
  });
  Object.values(src).forEach(function(s){ s.done = s.got >= s.qty; });
  return {
    move_id: t.move_id, day: t.day, at: iso(t.at), district: t.district,
    district_code: DCODE[t.district], district_name: DNAME[t.district],
    driver: t.driver, mine: !!mine && t.driver === ME.name,
    claimed_at: t.claimed_at ? iso(t.claimed_at) : '', started_at: t.started_at ? iso(t.started_at) : '',
    done_at: t.done_at ? iso(t.done_at) : '', cancelled_at: '',
    need: need, got: got, left: num(Math.max(0, need - got)), positions: lines.length,
    left_positions: lines.filter(l => !l.done).length,
    sources: Object.values(src).sort((a, b) => a.code < b.code ? -1 : 1), lines: lines,
    status: t.done_at ? 'done' : got > 0 ? 'live' : t.driver ? 'claimed' : 'free',
  };
}
function moves(){
  const mine = [], free = [], taken = [], give = [];
  if(!S.mv.done_at){
    const v = mvView(S.mv, true);
    (v.mine ? mine : v.driver ? taken : free).push(v);
  }
  if(!S.give.done_at){
    const g = mvView(S.give, false);
    give.push({...g, sources: [], to_code: g.district_code, to_name: g.district_name});
  }
  return {mine: mine, free: free, taken: taken, give: give};
}
function cashOnHand(){
  const got = S.ord.filter(o => o.delivered_at && !o.prepaid)
                   .reduce((a, o) => a + (o.settle ? o.settle.taken : o.total), 0);
  const spent = S.exp.filter(e => e.status !== 'rejected' && !e.plus).reduce((a, e) => a + e.amount, 0);
  const back = S.exp.filter(e => e.status !== 'rejected' && e.plus).reduce((a, e) => a + e.amount, 0);
  return {got: got, spent: spent, back: back, hand: num(got - spent + back)};
}
function shiftView(){
  const sh = S.sh;
  const route = S.ord.filter(o => !o.delivered_at).map(o => o.order_id);
  const sup = supView(S.sup);
  const intake = (!sup.done_at && !sup.noscan_at && sup.mine)
    ? [{sid: sup.supply_id, district: sup.district, code: sup.district_code, name: sup.district_name,
        need: sup.need, got: sup.got, left: sup.left, started: !!sup.started_at}] : [];
  const must = [], names = [];
  if(!S.noexp.fuel && !S.exp.some(e => e.kind === 'fuel')){ must.push('fuel'); names.push('Бензин'); }
  return {
    day: S.day, working: true, opened: sh.opened, opened_at: sh.opened_at ? iso(sh.opened_at) : '',
    closed: sh.closed, closed_at: sh.closed_at ? iso(sh.closed_at) : '',
    geo: {ok: true, fresh: true, stream: true, watch_ok: true, lost: false, still_sec: 45,
          age_sec: 20, endless: true, left_min: 0},
    must: sh.opened ? must : [], must_names: sh.opened ? names : [],
    in_route: route, intake: intake,
    can_open: !sh.opened && !sh.closed, can_close: sh.opened && !sh.closed,
    ...(S.ev.dayclose && now() > S.ev.dayclose
        ? {day_closed: true, day_closed_at: iso(S.ev.dayclose)} : {}),
    geo_bot: '', ...(sh.closed ? {after_close: true, report_day: S.day, report_closed_at: iso(sh.closed_at)} : {}),
  };
}
function summary(){
  const c = cashOnHand();
  const done = S.ord.filter(o => o.delivered_at);
  const cash = done.filter(o => !o.prepaid), app = done.filter(o => o.prepaid);
  const byKind = {};
  S.exp.forEach(function(e){
    const k = byKind[e.kind] || (byKind[e.kind] = {id: e.kind, t: KIND_T[e.kind] || 'Расход', plus: !!e.plus, aed: 0, n: 0});
    k.aed += e.amount; k.n++;
  });
  return {
    day: S.day, opened_at: S.sh.opened_at ? iso(S.sh.opened_at) : '', on_hand: c.hand,
    cash_taken: c.got, spent: c.spent, got: c.back, tips: 0, tips_cash: 0, tips_other: 0, tips_by: [],
    orders: done.length, gross: done.reduce((a, o) => a + o.total, 0),
    pay: {cash: {n: cash.length, aed: cash.reduce((a, o) => a + o.total, 0)},
          app: {n: app.length, aed: app.reduce((a, o) => a + o.total, 0)},
          crypto: {n: 0, aed: 0}, debt: {n: 0, aed: 0}, free: {n: 0, aed: 0}},
    expenses: Object.values(byKind), exp_n: S.exp.length,
    exp_pending: S.exp.filter(e => e.status === 'pending').length,
    writeoffs: S.wo.length, writeoff_qty: S.wo.length,
    ...(S.sh.closed ? {closed_at: iso(S.sh.closed_at)} : {}),
  };
}
const KIND_T = {fuel: 'Заправка', wash: 'Мойка', parking: 'Парковка', guard: 'Охрана', kfc: 'KFC · премия',
                we_gave: 'Мы вернули', owed_us: 'Нам должны', we_got: 'Нам вернули', we_owe: 'Мы должны',
                other: 'Что-то ещё'};
const KINDS = [{id: 'fuel', t: 'Заправка', receipt: true}, {id: 'wash', t: 'Мойка', receipt: true},
               {id: 'parking', t: 'Парковка', receipt: true}, {id: 'guard', t: 'Охрана'},
               {id: 'kfc', t: 'KFC · премия'}, {id: 'we_gave', t: 'Мы вернули'},
               {id: 'owed_us', t: 'Нам должны'}, {id: 'we_got', t: 'Нам вернули', plus: true},
               {id: 'we_owe', t: 'Мы должны', plus: true}, {id: 'other', t: 'Что-то ещё'}];

/* ═══ 5. Фальшивый сервер ═══════════════════════════════════════════════════
   Те же пути и те же ответы, что у боевого. Чего не знаем — отвечаем {ok},
   и путь уходит в журнал: так видно, если приложение обросло новой ручкой, а
   демо о ней не знает. */
function err(status, payload){
  const e = new Error('demo api ' + status);
  e.status = status; e.payload = payload || {};
  throw e;
}
const DEMO_LOG = [];
function route(path, opts){
  const m = (opts.method || 'GET').toUpperCase();
  const body = opts.body || {};
  const p = path.split('?')[0];
  tick();

  if(p === '/api/driver/ping')
    return {ok: true, driver: {...ME, test: false, test_allowed: false}, day: S.day, panic: false};
  if(p === '/api/driver/pos' || p === '/api/driver/panic') return {ok: true};

  /* ── смена ── */
  if(p === '/api/driver/shift' && m === 'GET') return shiftView();
  if(p === '/api/driver/shift/open'){
    S.sh.opened = true; S.sh.opened_at = now(); save();
    return shiftView();
  }
  if(p === '/api/driver/shift/summary') return summary();
  if(p === '/api/driver/shift/close'){
    const v = shiftView();
    if(v.in_route.length) err(409, {error: 'orders_in_route', ids: v.in_route});
    if(v.intake.length) err(409, {error: 'intake_open'});
    if(!S.mv.done_at && S.mv.driver === ME.name) err(409, {error: 'moves_open'});
    if(v.must.length) err(409, {error: 'expenses_left'});
    if(!v.day_closed) err(409, {error: 'day_open'});
    S.sh.closed = true; S.sh.closed_at = now(); save();
    return shiftView();
  }

  /* ── заказы ── */
  if(p === '/api/driver/orders' && m === 'GET'){
    const active = S.ord.filter(o => !o.delivered_at);
    const done = S.ord.filter(o => o.delivered_at);
    return {day: S.day, active: active, done: done,
            total_aed: done.reduce((a, o) => a + o.total, 0), panic: false,
            fx_take: [{code: 'USD', name: 'Доллар США', sym: '$', rate: 3.67},
                      {code: 'EUR', name: 'Евро', sym: '€', rate: 4.26},
                      {code: 'RUB', name: 'Рубль', sym: '₽', rate: 0.0435}]};
  }
  const om = p.match(/^\/api\/driver\/orders\/([^/]+)(\/.*)?$/);
  if(om){
    const oid = decodeURIComponent(om[1]);
    const tail = om[2] || '';
    const o = S.ord.find(x => x.order_id === oid);
    if(!o) err(404, {error: 'gone'});
    if(tail === '/ack'){ o.driver_ack_at = iso(now()); o.ack_ms = now(); save(); return {ok: true}; }
    if(tail === '/chat' && m === 'GET')
      return {order_id: oid, chat: S.chat[oid] || [], operator: 'Умар'};
    if(tail === '/chat'){
      const msg = {by: 'driver', name: ME.name, text: String(body.text || ''), at: iso(now()),
                   kind: body.kind || ''};
      (S.chat[oid] = S.chat[oid] || []).push(msg);
      o.chat_n = (o.chat_n || 0) + 1; o.chat_new = 0; save();
      // Оператор отвечает через несколько секунд — разговор в демо должен быть
      // разговором, а не стеной, в которую пишут.
      setTimeout(function(){
        (S.chat[oid] = S.chat[oid] || []).push({by: 'operator', name: 'Умар', at: iso(now()), kind: '',
          text: 'Принял, сейчас свяжусь с клиентом'});
        o.chat_n++; o.chat_new = 1; o.chat_last = {text: 'Принял, сейчас свяжусь с клиентом', at: iso(now()), name: 'Умар'};
        save();
      }, 6000);
      return {ok: true};
    }
    if(tail === '/fx'){
      const R = {USD: {name: 'Доллар США', sym: '$', rate: 3.67}, EUR: {name: 'Евро', sym: '€', rate: 4.26},
                 RUB: {name: 'Рубль', sym: '₽', rate: 0.0435}, GBP: {name: 'Фунт стерлингов', sym: '£', rate: 5.01}};
      const r = R[body.code];
      o.pay_fx = r ? {code: body.code, name: r.name, sym: r.sym, rate: r.rate,
                      amount: num(o.total / r.rate), at: iso(now())} : null;
      save();
      return {ok: true, pay_fx: o.pay_fx};
    }
    if(tail === '/settle'){
      const taken = +body.taken || 0;
      o.settle = {taken: taken, diff: num(taken - o.total), by: ME.name, at: iso(now()),
                  fx: body.fx || null};
      o.owed = num(o.total - taken);
      save();
      return {ok: true, taken: taken, diff: o.settle.diff};
    }
    if(tail === '/debt-back'){
      const back = Math.max(0, -(o.owed || 0));
      o.owed = 0; o.settle = null; save();
      return {ok: true, returned: back};
    }
    if(tail === '/delivered'){
      o.delivered_at = iso(now()); o.status = 'delivered'; save();
      return {ok: true};
    }
    if(tail === '/edit'){
      o.driver_req = {kind: body.kind || 'edit', status: 'open', text: body.text || '',
                      diff: body.diff || [], total: body.total || o.total, at: iso(now())};
      save();
      // Старший отвечает сам: просьба, которая висит вечно, ничего не показывает.
      setTimeout(function(){
        if(!o.driver_req) return;
        o.driver_req = null;
        if((body.kind || 'edit') === 'cancel'){ o.delivered_at = ''; }
        else if(Array.isArray(body.diff)){
          body.diff.forEach(function(d){
            if(d.kind === 'add') o.items.push({id: d.id || 'x', name: d.name, qty: d.qty || 1, pcs: null,
                                               price: d.price || 0, line_total: (d.price || 0) * (d.qty || 1), gift: false});
            if(d.kind === 'del') o.items = o.items.filter(i => i.name !== d.name);
          });
          o.total = o.items.reduce((a, i) => a + i.line_total, 0);
        }
        o.chat_n = (o.chat_n || 0) + 1; o.chat_new = 1;
        o.chat_last = {text: 'Правку согласовал', at: iso(now()), name: 'Умар'};
        (S.chat[oid] = S.chat[oid] || []).push({by: 'operator', name: 'Умар', text: 'Правку согласовал',
                                                at: iso(now()), kind: ''});
        save();
      }, 8000);
      return {ok: true};
    }
    if(tail === '/edit/withdraw'){ o.driver_req = null; save(); return {ok: true}; }
    return {ok: true};
  }

  /* ── приёмка ── */
  if(p === '/api/driver/supply' && m === 'GET'){
    const mine = S.sup.done_at || S.sup.noscan_at ? [] : [supView(S.sup)];
    const taken = S.supTaken.done_at ? [] : [supView(S.supTaken)];
    return {mine: mine, free: [], extra: [], taken: taken};
  }
  if(p === '/api/driver/supply/history')
    return {rows: (S.sup.done_at || S.sup.noscan_at) ? [supView(S.sup)] : []};
  const sm = p.match(/^\/api\/driver\/supply\/([^/]+)(\/.*)?$/);
  if(sm){
    const sid = sm[1], tail = sm[2] || '';
    const t = sid === S.sup.supply_id ? S.sup : sid === S.supTaken.supply_id ? S.supTaken : null;
    if(!t) err(404, {error: 'gone'});
    if(tail === '' && m === 'GET') return supView(t);
    if(tail === '/hold'){
      if(!t.started_at){ t.started_at = now(); save(); }   // «Начать сканирование» — замок правок
      return {ok: true, sec: 25, hold: {who: ME.name, kind: 'driver', live: false, mine: true}};
    }
    if(tail === '/scan'){
      const l = t.lines.find(x => x.id === body.product_id);
      if(!l) return {ok: false, verdict: 'not_in_task', code: body.code, label: ''};
      if(l.got >= l.need) return {ok: false, verdict: 'known', code: body.code, name: l.name, label: ''};
      l.got++; l.left = Math.max(0, l.need - l.got);
      S.scans++;
      const v = supView(t);
      if(v.left <= 0 && !t.done_at){ t.done_at = now(); }
      save();
      return {ok: true, got: l.got, left: l.left, need: l.need, name: l.name,
              label: 'B2-' + String(1000 + S.scans), task: supView(t), finished: !!t.done_at};
    }
    if(tail === '/noscan'){
      t.noscan_at = now(); t.done_at = t.done_at || now(); save();
      return {ok: true, ...supView(t)};
    }
    if(tail === '/claim'){ t.driver = ME.name; t.claimed_at = now(); save(); return {ok: true, ...supView(t)}; }
    if(tail === '/release'){ t.driver = ''; t.claimed_at = 0; t.started_at = 0; save(); return {ok: true}; }
    if(tail === '/finish'){ t.done_at = now(); save(); return {ok: true, ...supView(t)}; }
    if(tail === '/undo'){ return {ok: true, ...supView(t)}; }
    if(tail === '/buy'){ return supView(t); }
    return {ok: true};
  }

  /* ── перемещение между районами ── */
  if(p === '/api/driver/move' && m === 'GET') return moves();
  const mm = p.match(/^\/api\/driver\/move\/([^/]+)\/(claim|release|scan)$/);
  if(mm){
    const t = S.mv;
    const what = mm[2];
    if(what === 'claim'){
      if(t.driver && t.driver !== ME.name) return {ok: false, error: 'taken', driver: t.driver};
      t.driver = ME.name; t.claimed_at = now(); save();
      return {ok: true, task: mvView(t, true)};
    }
    if(what === 'release'){ t.driver = ''; t.claimed_at = 0; save(); return {ok: true}; }
    // Скан: код демонстрационный, товар в нём зашит — DEMO-<позиция>-<номер>.
    const pid = String(body.code || '').split('-')[1] || '';
    const l = t.lines.find(x => x.id === pid && x.got < x.qty);
    if(!l){
      const any = t.lines.find(x => x.id === pid);
      return {ok: false, verdict: any ? 'full' : 'not_in_task', code: body.code,
              name: (P[pid] || {}).name || ''};
    }
    if(!t.started_at) t.started_at = now();
    l.got = num(l.got + 1);
    const v = mvView(t, true);
    if(v.left <= 0 && !t.done_at){ t.done_at = now(); }
    save();
    const vv = mvView(t, true);
    return {ok: true, code: body.code, name: l.name, qty: 1, unit: 1, from_code: l.from_code,
            to_code: DCODE[t.district], line: vv.lines.find(x => x.id === pid), task: vv,
            finished: !!t.done_at};
  }

  /* ── расходы ── */
  if(p === '/api/driver/expenses' && m === 'GET'){
    const byKind = {};
    S.exp.forEach(function(e){
      const k = byKind[e.kind] || (byKind[e.kind] = {sum: 0, count: 0});
      k.sum += e.amount; k.count++;
    });
    return {day: S.day, drivers: [], totals: {}, held: [], held_total: 0, working: true,
            meal_rates: {working: 80, off: 40}, kinds: KINDS, extras: S.exp, by_kind: byKind,
            no_expense: S.noexp, pending_answer: S.asked};
  }
  if(p === '/api/driver/expenses' && m === 'POST'){
    if(body.none || body.no_expense){ S.noexp[body.kind] = true; save(); return {ok: true}; }
    if(body.car_del || body.del){
      S.exp = S.exp.filter(e => e.id !== body.id);
      save();
      return {ok: true};
    }
    const old = body.id ? S.exp.find(e => e.id === body.id) : null;
    if(old){
      old.amount = +body.amount || old.amount; old.comment = body.comment || old.comment;
      old.status = 'pending'; old.at_ms = now();
    }else{
      S.exp.push({id: 'e' + (++S.seq), kind: body.kind, kind_t: KIND_T[body.kind] || 'Расход',
                  amount: +body.amount || 0, comment: body.comment || '', status: 'pending',
                  photo: body.photo ? 'p' : '', thumb: body.thumb || '', car_photo: body.car_photo ? 'p' : '',
                  plus: !!(KINDS.find(k => k.id === body.kind) || {}).plus, at: iso(now()), at_ms: now()});
      delete S.noexp[body.kind];
    }
    save();
    return {ok: true};
  }

  /* ── бой, охрана, склад ── */
  if(p === '/api/driver/writeoffs') return {rows: S.wo, day: S.day};
  if(p === '/api/driver/writeoff/scan'){
    const pid = String(body.code || '').split('-')[1] || 'p1';
    const pr = P[pid] || P.p1;
    return {ok: true, code: body.code, label: 'B2-1042', name: pr.name, img: '', cat: pr.cat,
            price: pr.price, district: {id: 'jvc', code: 'B2', name: 'JVC'}, districts: DISTR};
  }
  if(p === '/api/driver/writeoff'){
    S.wo.push({id: 'w' + (++S.seq), name: (P[String(body.code || '').split('-')[1]] || P.p1).name,
               qty: 1, reason: body.reason || '', at: iso(now()), status: 'pending'});
    save();
    return {ok: true};
  }
  if(p === '/api/driver/bottle'){
    const code = (opts.params || {}).code || '';
    const pid = String(code).split('-')[1] || '';
    return P[pid] ? {ok: true, bottle: P[pid].name, cost: P[pid].buy}
                  : {ok: false, say: 'не наша бутылка'};
  }

  /* ── справочники ── */
  if(p === '/api/driver/catalog')
    return {items: Object.values(P).map(function(x){
              return {id: x.id, name: x.name, cat: x.cat, price: x.price, app: x.price, pack: !!x.pack,
                      p12: x.pack ? 100 : 0, p24: x.pack ? 200 : 0, app12: x.pack ? 100 : 0, app24: x.pack ? 200 : 0};
            }), cats: ['Водка', 'Джин', 'Виски', 'Пиво']};
  if(p === '/api/driver/rates') return RATES;
  if(p === '/api/driver/profile') return PROFILE();
  if(p === '/api/driver/history') return HISTORY();

  DEMO_LOG.push(m + ' ' + p);
  if(DEMO_LOG.length < 40) console.log('[демо] ручка без ответа:', m, p);
  return {ok: true};
}

const RATES = {ok: true, at: iso(now() - 40 * 60000), silent: false, days: 6, rates: [
  {code: 'USD', name: 'Доллар США', sym: '$', rate: 3.673, market: 3.6725, cash: true, main: true, kind: 'fiat', prev: 3.6686, change: 0.12, spark: [3.668, 3.669, 3.6705, 3.669, 3.6715, 3.673]},
  {code: 'EUR', name: 'Евро', sym: '€', rate: 4.261, market: 4.258, cash: true, main: true, kind: 'fiat', prev: 4.2644, change: -0.08, spark: [4.272, 4.269, 4.2655, 4.267, 4.2644, 4.261]},
  {code: 'GBP', name: 'Фунт стерлингов', sym: '£', rate: 5.015, market: 5.01, cash: false, main: true, kind: 'fiat', prev: 5.0075, change: 0.15, spark: [5.0, 5.004, 5.002, 5.009, 5.0075, 5.015]},
  {code: 'RUB', name: 'Рубль', sym: '₽', rate: 0.0435, market: 0.0433, cash: true, main: true, kind: 'fiat', prev: 0.0436, change: -0.21, spark: [0.0441, 0.0439, 0.0438, 0.0437, 0.0436, 0.0435]},
  {code: 'TRY', name: 'Турецкая лира', sym: '₺', rate: 0.0756, market: 0.0755, cash: false, main: true, kind: 'fiat', prev: 0.0754, change: 0.32, spark: [0.0749, 0.0751, 0.075, 0.0753, 0.0754, 0.0756]},
  {code: 'USDT', name: 'Tether USDT', sym: '', rate: 3.673, market: 3.673, cash: false, main: true, kind: 'crypto', prev: 3.6716, change: 0.04, spark: [3.671, 3.6705, 3.672, 3.6715, 3.6716, 3.673]}]};

function PROFILE(){
  const mon = S.day.slice(0, 7);
  return {month: mon, name: ME.name, role: 'driver', role_t: 'Водитель', code: ME.district_code,
          district: ME.district, district_name: ME.district_name, since: '2025-11-04', active: true,
          rate: 5000, cur: 'AED', unit: 'month', days: 26, accrued: 5000, plus: 0, minus: 400,
          fines: 0, holds: 400, to_pay: 4600, paid: 0, left: 4600, debt: 0, month_total: 400, month_count: 1,
          items: [{id: 'p1', kind: 'hold', t: 'Удержание', amount: 400, per_month: 0, day: S.day,
                   at: iso(now() - 3 * 24 * 3600000), reason: '', note: 'Аванс на топливо', due: 400,
                   left: 0, done: true, cancelled: false}]};
}
function HISTORY(){
  const done = S.ord.filter(o => o.delivered_at);
  if(!done.length) return {ok: true, today: S.day, days: []};
  return {ok: true, today: S.day, days: [{
    day: S.day, count: done.length, aed: done.reduce((a, o) => a + o.total, 0),
    cash: done.filter(o => !o.prepaid).reduce((a, o) => a + o.total, 0),
    online: done.filter(o => o.prepaid).reduce((a, o) => a + o.total, 0), avg_min: 21,
    orders: done.map(function(o){
      return {order_id: o.order_id, address: o.address, total: o.total, delivered_at: o.delivered_at,
              timestamp: o.timestamp, items: o.items, mins: 21, pay_fx: o.pay_fx, prepaid: !!o.prepaid,
              payment_method: o.payment_method, district: o.district};
    })}]};
}

window.drvApi = {AMBAR_API: '', drvFetch: async function(path, opts){
  opts = opts || {};
  await new Promise(r => setTimeout(r, 90));      // сеть не бывает мгновенной
  return route(path, opts);
}};

/* ═══ 6. Камера ════════════════════════════════════════════════════════════
   Ни одного разрешения не спрашиваем: вместо камеры — холст. Он нужен и
   сканеру (кадр всё равно подменяем кодом), и снимку чека: приложение
   вырезает кадр из видео, и на чек попадает подпись «ДЕМО». */
let CV = null, STREAM = null;
function demoStream(){
  if(STREAM) return STREAM;
  CV = document.createElement('canvas'); CV.width = 720; CV.height = 1280;
  const ctx = CV.getContext('2d');
  let i = 0;
  setInterval(function(){
    i++;
    ctx.fillStyle = '#17171f'; ctx.fillRect(0, 0, CV.width, CV.height);
    const g = ctx.createRadialGradient(360, 620, 40, 360, 620, 520);
    g.addColorStop(0, 'rgba(201,169,110,.22)'); g.addColorStop(1, 'rgba(201,169,110,.02)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, CV.width, CV.height);
    ctx.strokeStyle = 'rgba(255,255,255,.07)'; ctx.lineWidth = 2;
    for(let y = 120; y < CV.height; y += 90){
      ctx.beginPath(); ctx.moveTo(90, y); ctx.lineTo(630, y); ctx.stroke();
    }
    ctx.fillStyle = 'rgba(201,169,110,' + (0.75 + 0.25 * Math.sin(i / 7)) + ')';
    ctx.textAlign = 'center';
    ctx.font = 'bold 52px -apple-system,Helvetica,Arial,sans-serif';
    ctx.fillText('ДЕМО', 360, 150);
  }, 120);
  STREAM = CV.captureStream(10);
  return STREAM;
}
function fakeCam(){
  const md = {
    getUserMedia: async function(){ return demoStream().clone(); },
    enumerateDevices: async function(){ return []; },
    getSupportedConstraints: function(){ return {}; },
    addEventListener: noop, removeEventListener: noop,
  };
  try{
    if(navigator.mediaDevices){ navigator.mediaDevices.getUserMedia = md.getUserMedia;
                                navigator.mediaDevices.enumerateDevices = md.enumerateDevices; }
    else Object.defineProperty(navigator, 'mediaDevices', {value: md, configurable: true});
  }catch(e){
    try{ Object.defineProperty(navigator, 'mediaDevices', {value: md, configurable: true}); }catch(e2){}
  }
}

/* ═══ 7. Сканер ════════════════════════════════════════════════════════════
   Настоящий SCAN ищет код в кадре. Здесь кадр рисованный, поэтому код даём
   сами — и не любой, а тот, который сейчас имеет смысл: бутылку открытой
   позиции приёмки, следующую позицию перемещения, бутылку для боя и охраны.
   Приложение об этом не знает: ему приходит строка из «камеры», как в бою. */
function nextCode(id){
  S.scans++;
  const one = (pid) => 'DEMO-' + pid + '-' + S.scans;
  if(id === 'camV'){                                  // приёмка
    try{ if(typeof LINE !== 'undefined' && LINE && LINE.id) return one(LINE.id); }catch(e){}
    return null;
  }
  if(id === 'mvV'){                                   // перемещение по заявке
    const l = S.mv.lines.find(x => x.got < x.qty);
    return l ? one(l.id) : one('p1');
  }
  return one('p1');                                   // бой и бутылка охране
}
function patchScan(){
  if(typeof SCAN === 'undefined' || !SCAN) return;
  SCAN.start = async function(videoEl, onCode, onIdle, onRepeat, onHold){
    this.stop();
    this.seen = new Set(); this.seenAt = new Map();
    this.video = videoEl;
    try{ videoEl.srcObject = demoStream().clone(); await videoEl.play(); }catch(e){}
    const self = this;
    // Первый код — через полторы секунды: человек должен успеть увидеть, что
    // камера открылась и что он наводит её на бутылку.
    this._t = setInterval(function(){
      const code = nextCode(videoEl.id);
      if(!code){ if(onIdle) onIdle(1200, 30, 20); return; }
      if(self.seen.has(code)) return;
      self.seen.add(code); self.seenAt.set(code, Date.now());
      if(onHold) onHold(code);
      if(onCode) onCode(code);
    }, 1600);
    return true;
  };
  SCAN.stop = function(){
    if(this._t){ clearInterval(this._t); this._t = null; }
    if(this.video){ try{ this.video.pause(); }catch(e){}
      try{ const s = this.video.srcObject; if(s) s.getTracks().forEach(t => t.stop()); }catch(e){}
      this.video.srcObject = null; }
  };
}

/* ═══ 8. Плашка «ДЕМО» и первый экран ══════════════════════════════════════ */
const CSS = `
.dmo-card{padding:18px 18px 16px;margin-top:12px}
.dmo-card-t{font:700 15px/1.2 -apple-system,Helvetica,Arial,sans-serif;color:#c9a96e;letter-spacing:.02em}
.dmo-card-s{margin-top:6px;font-size:13px;line-height:1.45;color:rgba(255,255,255,.55)}
.dmo-again{color:#c9a96e !important;border:1px solid rgba(201,169,110,.45) !important}
.dmo-card-b{margin-top:14px;width:100%;height:44px;border:1px solid rgba(201,169,110,.45);
  border-radius:13px;background:transparent;color:#c9a96e;cursor:pointer;
  font:600 14px/1 -apple-system,Helvetica,Arial,sans-serif}
.dmo-w{position:fixed;inset:0;z-index:300;display:flex;align-items:flex-end;justify-content:center;
  background:rgba(4,4,10,.72);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.dmo-c{width:calc(100% - 24px);max-width:520px;margin:0 12px calc(14px + env(safe-area-inset-bottom));
  background:#0e0e18;border:1px solid rgba(201,169,110,.28);border-radius:22px;padding:22px 20px 18px;
  color:#fff;font-family:-apple-system,Helvetica,Arial,sans-serif}
.dmo-c h4{margin:0 0 4px;font:700 19px/1.2 -apple-system,Helvetica,Arial,sans-serif;color:#c9a96e;
  letter-spacing:.02em}
.dmo-c p{margin:0 0 14px;font-size:13.5px;line-height:1.45;color:rgba(255,255,255,.62)}
.dmo-c ul{margin:0 0 16px;padding:0;list-style:none}
.dmo-c li{position:relative;padding:0 0 9px 16px;font-size:13.5px;line-height:1.4;color:rgba(255,255,255,.86)}
.dmo-c li:before{content:'';position:absolute;left:0;top:7px;width:5px;height:5px;border-radius:50%;
  background:#c9a96e}
.dmo-c button{width:100%;height:48px;border:0;border-radius:14px;background:linear-gradient(135deg,#c9a96e,#e6cf9b);
  color:#14140f;font:700 15px/1 -apple-system,Helvetica,Arial,sans-serif;cursor:pointer}
.dmo-c button.gh{margin-top:8px;background:transparent;border:1px solid rgba(255,255,255,.18);
  color:rgba(255,255,255,.7);height:44px;font-weight:600}
`;
function sheet(html){
  const w = document.createElement('div');
  w.className = 'dmo-w';
  w.innerHTML = '<div class="dmo-c">' + html + '</div>';
  w.addEventListener('click', function(e){ if(e.target === w) w.remove(); });
  document.body.appendChild(w);
  return w;
}
function askReset(){
  const w = sheet('<h4>Начать демо заново</h4>'
    + '<p>Смена, заказы, приёмка и расходы вернутся в начало. Настоящих данных это не касается — их здесь нет.</p>'
    + '<button id="dmoYes">Начать заново</button>'
    + '<button class="gh" id="dmoNo">Отмена</button>');
  w.querySelector('#dmoYes').onclick = window.demoReset;
  w.querySelector('#dmoNo').onclick = function(){ w.remove(); };
}
// Закрытая смена — полный экран без вкладок: заново её не открыть, и демо
// упёрлось бы в тупик. Дорисовываем туда свою кнопку «Начать заново».
function patchShift(){
  const orig = window.shPaint;
  if(typeof orig !== 'function') return;
  window.shPaint = function(){
    orig.apply(this, arguments);
    const bot = document.querySelector('#shScr .shscr-bot');
    const closed = (typeof SH !== 'undefined' && SH && SH.closed);
    let b = document.getElementById('dmoAgain');
    if(!bot) return;
    if(!closed){ if(b) b.remove(); return; }
    if(b) return;
    b = document.createElement('button');
    b.id = 'dmoAgain'; b.className = 'shscr-alt dmo-again';
    b.textContent = 'Начать демо заново';
    b.onclick = askReset;
    bot.appendChild(b);
  };
}
// «Начать заново» живёт в профиле, а не плашкой поверх экрана: любая плашка
// внизу закрывает «Доставил» и «Чат», а это главные кнопки водителя.
function patchProfile(){
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
  const orig = window.profPaint;
  if(typeof orig !== 'function') return;
  window.profPaint = function(){
    orig.apply(this, arguments);
    try{ if(typeof PROF_PAGE !== 'undefined' && PROF_PAGE) return; }catch(e){}
    const box = document.getElementById('body');
    if(!box || box.querySelector('.dmo-card')) return;
    const c = document.createElement('div');
    c.className = 'card dmo-card';
    c.innerHTML = '<div class="dmo-card-t">Демо-режим</div>'
      + '<div class="dmo-card-s">Заказы, приёмка и деньги здесь придуманы и живут только в этом телефоне.'
      + ' Настоящих данных демо не видит и ничего никуда не отправляет.</div>'
      + '<button class="dmo-card-b">Начать заново</button>';
    c.querySelector('button').onclick = askReset;
    box.appendChild(c);
  };
}
function intro(){
  if(LS.get(KEY + '_seen') === '1') return;
  LS.set(KEY + '_seen', '1');
  const w = sheet('<h4>Это демо приложения водителя</h4>'
    + '<p>Всё настоящее, кроме данных: заказы, приёмка и деньги придуманы и живут только в этом телефоне.</p>'
    + '<ul><li>Откройте смену — через несколько секунд придёт заказ.</li>'
    + '<li>«Товар» — приёмка и перемещение между районами: сканер работает сам, камеру наводить не нужно.</li>'
    + '<li>«Расходы» — заправка с чеком уходит старшему на согласование.</li>'
    + '<li>Смена не закроется, пока заказ не доставлен, а приёмка не завершена, — как в бою.</li></ul>'
    + '<button id="dmoGo">Начать</button>');
  w.querySelector('#dmoGo').onclick = function(){ w.remove(); };
}

/* ═══ 9. Запуск ════════════════════════════════════════════════════════════
   Дальше работает боевой boot(): тот же ping, та же загрузка смены и заказов,
   те же опросы раз в пять секунд. Мы только чиним камеру, сканер и звонок —
   всё, чего нет в браузере без телеграма. */
fakeCam();
window.demoBoot = async function(){
  patchScan();
  try{ window.AmbarCall = null; }catch(e){}        // звонок без сервера не поднять
  patchProfile();
  patchShift();
  try{ await boot(); }catch(e){ console.error('[демо] boot:', e); }
  try{
    window.callOpen = function(){
      if(typeof toast === 'function') toast('В демо звонок не идёт. В бою здесь оператор и старший', 'err');
    };
  }catch(e){}
  intro();
};
})();
