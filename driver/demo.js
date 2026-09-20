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
// Внутри телеграма LocationManager подменить нельзя — он прибит к объекту
// намертво (неперезаписываемое свойство). Поэтому геопозицию демо подставляет
// выше: в самом приложении, в geoReady/geoRead (см. patchGeo).
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
   перемещение в обе стороны; обязательный ответ про заправку. */
function line(pid, n, unit){
  const p = P[pid];
  return {id: p.id, name: p.name, need: n, got: 0, left: n, qty_total: n, unit_n: unit || 1,
          unit_name: unit && unit > 1 ? 'коробку' : 'бутылку', units: n, price: 0};
}
function mvLine(pid, from, qty, got){
  return {from: from, from_code: DCODE[from], from_name: DNAME[from], id: pid, name: P[pid].name,
          unit: 1, qty: qty, got: got || 0, left: qty - (got || 0), done: (got || 0) >= qty, recv: 0};
}
function fresh(){
  const t = now();
  return {
    v: 2, day: day(), born: t,
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
    // Перемещение (владелец, 18 сен 2026): сканирует тот, кто отдаёт; с 19 сен
    // сканирует и получатель — каждую бутылку, последний скан принимает сам.
    // Демо-водитель — на обеих сторонах: отдаёт в Бизнес Бей и принимает из
    // Бизнес Бея и Алгусеса. Из Бизнес Бея уже отдали всё — «Принять —
    // сканировать» можно сразу; Алгусес начнёт отдавать через полминуты, и
    // серая карточка оживёт на глазах.
    mvIn: {move_id: 'MV260918-01', at: t - 50 * 60000, pairs: {
      bbay:    {giver: 'Авазбек', lines: [mvLine('p14', 'bbay', 4, 4)], acc: null},
      alguses: {giver: '', lines: [mvLine('p1', 'alguses', 3), mvLine('p55', 'alguses', 2)], acc: null,
                start: t + 30000},
    }},
    mvOut: {move_id: 'MV260918-02', at: t - 40 * 60000, to: 'bbay', giver: '',
            lines: [mvLine('p6', 'jvc', 2)]},
    exp: [], noexp: {}, asked: [], wo: [], seq: 0, scans: 0,
    cr: null,               // просьба закрыть смену раньше
    // Ревизия района (владелец, 19 сен 2026): та же, что у старшего, без денег.
    aud: {state: 'idle', started_at: 0, started_by: '', finished_at: 0, finished_by: '',
          note: '', codes: {}},
  };
}
let S = (function(){
  try{
    const raw = LS.get(KEY);
    const s = raw ? JSON.parse(raw) : null;
    if(s && s.v === 2 && s.day === day()) return s;
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
  // Просьба закрыть смену раньше: в демо оператор отвечает сам через
  // пятнадцать секунд — отпускает, питание оставляет 80.
  if(S.cr && S.cr.status === 'open' && now() - S.cr.at_ms > 15000){
    Object.assign(S.cr, {status: 'ok', by: 'Умар', decided_at: iso(now()), meal: 80});
  }
  // Смену района закрывает оператор, и только после этого водитель закрывает
  // свою. В демо оператор «просыпается» через полторы минуты после последней
  // доставки — чтобы успеть попробовать и «попросить закрыть раньше»; пока
  // такая просьба ждёт ответа, район он не закрывает.
  if(!S.ev.dayclose && S.ord.length >= 2 && S.ord.every(o => o.delivered_at)
     && !(S.cr && S.cr.status === 'open')) S.ev.dayclose = now() + 90000;
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
// Алгусес отдаёт сам: через полминуты после начала демо Даврон начинает и
// сканирует по бутылке в пять секунд — у получателя это видно живьём.
function simIn(){
  const pr = S.mvIn.pairs.alguses;
  if(!pr || !pr.start || now() < pr.start || pr.acc) return;
  pr.giver = 'Даврон';
  let n = Math.floor((now() - pr.start) / 5000) + 1;
  pr.lines.forEach(function(l){ const k = Math.min(l.qty, Math.max(l.got, n)); n -= k; l.got = k; });
}
// Передача одной пары «откуда → куда» — как отдаёт её сервер.
function pairView(mid, at, to, from, giver, lines, acc){
  const ls = lines.map(function(l){ const rv = +l.recv || 0;
    return {...l, left: num(Math.max(0, l.qty - l.got)), done: l.got >= l.qty,
            recv: num(rv), recv_left: num(Math.max(0, l.got - rv))}; });
  const need = num(ls.reduce((a, l) => a + l.qty, 0)), got = num(ls.reduce((a, l) => a + l.got, 0));
  const recv = num(ls.reduce((a, l) => a + l.recv, 0));
  return {
    move_id: mid, day: S.day, at: iso(at), by: 'STAR', note: '',
    district: to, district_code: DCODE[to], district_name: DNAME[to], to_code: DCODE[to], to_name: DNAME[to],
    from: from, from_code: DCODE[from], from_name: DNAME[from],
    giver: giver, mine: giver === ME.name, driver: '', started_at: '', given_at: '',
    accepted_at: acc ? iso(now()) : '', accepted_by: acc ? ME.name : '', accept_ok: acc ? !!acc.ok : null,
    accept_lines: acc ? acc.lines || [] : [], accept_note: acc ? acc.note || '' : '',
    need: need, got: got, left: num(Math.max(0, need - got)), positions: ls.length,
    recv: recv, recv_left: num(Math.max(0, got - recv)),
    left_positions: ls.filter(l => !l.done).length, sources: [], lines: ls,
    status: acc ? (acc.ok ? 'done' : 'diff') : got >= need ? 'given' : giver ? 'live' : 'wait',
  };
}
function outView(){ const o = S.mvOut; return pairView(o.move_id, o.at, o.to, ME.district, o.giver, o.lines, null); }
function inView(src){ const p = S.mvIn.pairs[src]; return pairView(S.mvIn.move_id, S.mvIn.at, ME.district, src, p.giver, p.lines, p.acc); }
function moves(){
  simIn();
  const give = [], take = [];
  const g = outView();
  if(g.status === 'wait' || g.status === 'live') give.push(g);
  Object.keys(S.mvIn.pairs).forEach(function(src){
    const v = inView(src);
    if(v.status !== 'done' && v.status !== 'diff') take.push(v);
  });
  take.sort((a, b) => (a.status === 'given' ? 0 : 1) - (b.status === 'given' ? 0 : 1));
  return {give: give, take: take, mine: [], free: [], taken: []};
}
// Что держит смену: отдать — пока не отсканировано всё, принять — пока не принято.
function movesLeft(){
  const r = moves();
  const row = (v, side) => ({move_id: v.move_id, side: side, district: v.district, from: v.from,
    code: side === 'give' ? v.to_code : v.from_code, name: side === 'give' ? v.to_name : v.from_name,
    status: v.status, giver: v.giver, need: v.need, got: v.got, left: v.left, positions: v.left_positions});
  return r.give.map(v => row(v, 'give')).concat(r.take.map(v => row(v, 'take')));
}
function cashOnHand(){
  const got = S.ord.filter(o => o.delivered_at && !o.prepaid)
                   .reduce((a, o) => a + (o.settle ? o.settle.taken : o.total), 0);
  // Безналом — наличные не тронуты: в «на руках» не входит (как у боевого).
  const live = S.exp.filter(e => e.status !== 'rejected');
  const sum = f => live.filter(f).reduce((a, e) => a + e.amount, 0);
  const spent = sum(e => !e.plus && e.pay !== 'card'), back = sum(e => e.plus && e.pay !== 'card');
  return {got: got, spent: spent, back: back, hand: num(got - spent + back),
          spent_card: sum(e => !e.plus && e.pay === 'card'), back_card: sum(e => e.plus && e.pay === 'card')};
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
    moves: sh.opened && !sh.closed ? movesLeft() : [],
    can_open: !sh.opened && !sh.closed, can_close: sh.opened && !sh.closed,
    ...(S.ev.dayclose && now() > S.ev.dayclose
        ? {day_closed: true, day_closed_at: iso(S.ev.dayclose)} : {}),
    geo_bot: '', ...(sh.closed ? {after_close: true, report_day: S.day, report_closed_at: iso(sh.closed_at)} : {}),
    close_req: S.cr ? {id: S.cr.id, status: S.cr.status, at: iso(S.cr.at_ms), reason: S.cr.reason,
                       reason_t: S.cr.reason_t, to: 'Умар', by: S.cr.by || '', decided_at: S.cr.decided_at || '',
                       meal: S.cr.meal || null, note: '', no_reason_t: '', escalated: false, next_at: ''} : null,
    released: !!(S.cr && S.cr.status === 'ok'), operator: 'Умар',
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
    cash_taken: c.got, spent: c.spent, got: c.back, spent_card: c.spent_card, got_card: c.back_card,
    tips: 0, tips_cash: 0, tips_other: 0, tips_by: [],
    orders: done.length, gross: done.reduce((a, o) => a + o.total, 0),
    pay: {cash: {n: cash.length, aed: cash.reduce((a, o) => a + o.total, 0)},
          app: {n: app.length, aed: app.reduce((a, o) => a + o.total, 0)},
          crypto: {n: 0, aed: 0}, debt: {n: 0, aed: 0}, free: {n: 0, aed: 0}},
    expenses: Object.values(byKind), exp_n: S.exp.length,
    hand: demoHand(done),
    exp_pending: S.exp.filter(e => e.status === 'pending').length,
    writeoffs: S.wo.length, writeoff_qty: S.wo.length,
    ...(S.sh.closed ? {closed_at: iso(S.sh.closed_at)} : {}),
  };
}
/* ── ревизия района ──────────────────────────────────────────────────────
   На полке демо-района числится десять единиц с QR-кодом: четыре Absolut,
   три Gordon's (и ещё одна бутылка без кода — камера её не видит), два
   Jameson и коробка пива (код пива = полкоробки). Камера показа находит не
   всё: второго Jameson на полке нет, зато попадается бутылка, числящаяся на
   B1. Итог выходит как в жизни — с недостачей и с лишней. */
const AUD_SHELF = [
  {id: 'p1',  no: 1, unit: 1,  coded: 4, noqr: 0},
  {id: 'p55', no: 2, unit: 1,  coded: 3, noqr: 1},
  {id: 'p14', no: 3, unit: 1,  coded: 2, noqr: 0},
  {id: 'p31', no: 4, unit: 12, coded: 1, noqr: 0},
];
const AUD_HOME = 'alguses';                     // чей чужой код попадётся
const AUD_SEES = ['p1', 'p1', 'p1', 'p1', 'p55', 'p55', 'p55', 'p14',
                  'p31', 'p31', 'p12'];         // порядок бутылок на полке
const audQty = pid => (P[pid] && P[pid].pack) ? 0.5 : 1;
const audOur = pid => AUD_SHELF.some(r => r.id === pid);
function audCount(pid){
  let q = 0;
  Object.keys(S.aud.codes).forEach(function(c){ if(S.aud.codes[c] === pid) q += audQty(pid); });
  return num(q);
}
function audRows(){
  const rows = AUD_SHELF.map(r => ({id: r.id, name: P[r.id].name, no: r.no, unit: r.unit,
                                    coded: r.coded, actual: audCount(r.id), noqr: r.noqr}));
  Object.keys(S.aud.codes).forEach(function(c){
    const pid = S.aud.codes[c];
    if(audOur(pid) || rows.some(r => r.id === pid)) return;
    rows.push({id: pid, name: P[pid] ? P[pid].name : 'Бутылка', no: rows.length + 1,
               unit: 1, coded: 0, actual: audCount(pid), noqr: 0});
  });
  return rows;
}
function audView(rows){
  const a = S.aud, short = [], over = [];
  rows.forEach(function(r){
    const d = num(r.coded - r.actual);
    if(d > 0) short.push({id: r.id, name: r.name, qty: d, unit: r.unit});
    else if(d < 0) over.push({id: r.id, name: r.name, qty: -d, unit: r.unit,
                              homes: [DCODE[AUD_HOME]], written: 0, sold: 0});
  });
  const done = a.state === 'pending' || a.state === 'closed';
  return {state: a.state, started_at: a.started_at ? iso(a.started_at) : '',
          started_by: a.started_by, finished_at: a.finished_at ? iso(a.finished_at) : '',
          finished_by: a.finished_by, finished_kind: a.finished_at ? 'driver' : '',
          note: a.note, closed_at: '', alien: 0,
          short: done ? short : [], over: done ? over : [],
          short_done: false, over_done: false};
}
function audFull(){
  const rows = audRows();
  const t = {coded: 0, actual: 0, noqr: 0, short: 0, over: 0};
  rows.forEach(function(r){
    t.coded += r.coded; t.actual += r.actual; t.noqr += r.noqr;
    const d = r.coded - r.actual;
    if(d > 0) t.short += d; else if(d < 0) t.over += -d;
  });
  Object.keys(t).forEach(function(k){ t[k] = num(t[k]); });
  return {district: ME.district, district_code: ME.district_code, district_name: ME.district_name,
          day: S.day, coded_codes: AUD_SHELF.reduce((a, r) => a + r.coded, 0),
          rows: rows, totals: t, scan: {total: Object.keys(S.aud.codes).length, odd: 0},
          audit: audView(rows)};
}

// Две пачки — как на боевом сервере (cash_math.piles): выручка (валюта как
// есть + дирхамы) и чай операторов; питание и бонус остаются водителю.
function demoHand(done){
  const fx = {}; let aed = 0, n = 0;
  done.filter(o => !o.prepaid).forEach(function(o){
    n++;
    const s = o.settle || null;
    if(s && s.fx){ const x = fx[s.fx.code] || (fx[s.fx.code] = {code: s.fx.code, sym: s.fx.sym || s.fx.code, amount: 0, aed: 0});
                   x.amount = num(x.amount + (+s.fx.amount || 0)); x.aed = num(x.aed + (+s.taken || 0)); return; }
    if(!s && o.pay_fx){ const f = o.pay_fx, x = fx[f.code] || (fx[f.code] = {code: f.code, sym: f.sym || f.code, amount: 0, aed: 0});
                        x.amount = num(x.amount + num(o.total / f.rate)); x.aed = num(x.aed + o.total); return; }
    aed += s ? (+s.taken || 0) : o.total;
  });
  const live = S.exp.filter(e => e.status !== 'rejected');
  const spent = {}, gotk = {}; let got = 0, bonus = 0, cs = 0, cg = 0, pend = 0;
  live.forEach(function(e){
    if(e.kind === 'upsell'){ bonus += e.amount; return; }
    if(e.pay === 'card'){ if(e.plus) cg += e.amount; else cs += e.amount; return; }
    const имя = KIND_T[e.kind] || 'Расход';
    if(e.plus) { got += e.amount; gotk[имя] = (gotk[имя] || 0) + e.amount; }
    else spent[имя] = (spent[имя] || 0) + e.amount;
    if(e.status === 'pending') pend++;
  });
  const fxs = Object.values(fx), fxAed = fxs.reduce((a, x) => a + x.aed, 0);
  const sp = Object.values(spent).reduce((a, v) => a + v, 0), meal = S.sh.opened ? 80 : 0, tea = 0;
  const taken = num(aed + fxAed), revenue = num(taken - tea - sp - meal - bonus + got);
  return {taken: taken, taken_aed: num(aed), orders_cash: n, fx: fxs, tea: tea, tea_other: 0, tea_by: [],
          spent: Object.keys(spent).map(k => ({t: k, aed: spent[k]})), spent_sum: sp, got: got,
          got_list: Object.keys(gotk).map(k => ({t: k, aed: gotk[k]})), meal: meal,
          bonus: bonus, card_spent: cs, card_got: cg, pending: pend, revenue: revenue,
          revenue_aed: num(revenue - fxAed), keep: meal + bonus, in_hand: num(revenue + tea + meal + bonus)};
}
const KIND_T = {fuel: 'Заправка', wash: 'Мойка', parking: 'Парковка', guard: 'Охрана', kfc: 'KFC · премия',
                we_gave: 'Мы вернули', owed_us: 'Нам должны', we_got: 'Нам вернули', we_owe: 'Мы должны',
                other: 'Что-то ещё'};
// pay — спрашивать ли «как платили» (у охраны бутылка или наличные, «нам
// должны» — не платёж), как у боевого сервера.
const KINDS = [{id: 'fuel', t: 'Заправка', receipt: true, pay: true}, {id: 'wash', t: 'Мойка', receipt: true, pay: true},
               {id: 'parking', t: 'Парковка', receipt: true, pay: true}, {id: 'guard', t: 'Охрана', pay: false},
               {id: 'kfc', t: 'KFC · премия', pay: true}, {id: 'we_gave', t: 'Мы вернули', pay: false},
               {id: 'owed_us', t: 'Нам должны', pay: false}, {id: 'we_got', t: 'Нам вернули', plus: true, pay: false},
               {id: 'we_owe', t: 'Мы должны', plus: true, pay: false},
               {id: 'advance', t: 'Аванс зарплаты', pay: false}, {id: 'other', t: 'Что-то ещё', pay: true}];

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
  if(p === '/api/driver/shift/close-request'){
    const v = shiftView();
    if(v.in_route.length) err(409, {error: 'orders_in_route', ids: v.in_route});
    if(S.cr && S.cr.status === 'open') err(409, {error: 'exists'});
    const R = {shift_end: 'Моя смена закончилась', sick: 'Плохо себя чувствую', car: 'Проблема с машиной', other: 'Другое'};
    const t = String(body.text || '').trim();
    S.cr = {id: 'c' + (++S.seq), status: 'open', at_ms: now(), reason: body.reason,
            reason_t: body.reason === 'other' && t ? t : (R[body.reason] || '') + (t && body.reason !== 'other' ? ' · ' + t : '')};
    save();
    return {ok: true, shift: shiftView()};
  }
  if(p === '/api/driver/shift/close-request/withdraw'){
    if(S.cr && S.cr.status === 'open') S.cr.status = 'withdrawn';
    save();
    return {ok: true, shift: shiftView()};
  }
  if(p === '/api/driver/shift/close'){
    const v = shiftView();
    if(v.in_route.length) err(409, {error: 'orders_in_route', ids: v.in_route});
    if(v.intake.length) err(409, {error: 'intake_open'});
    if(movesLeft().length) err(409, {error: 'moves_open'});
    if(v.must.length) err(409, {error: 'expenses_left'});
    if(!v.day_closed && !v.released) err(409, {error: 'day_open'});
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
    // «На месте»: настоящий сервер шлёт оператору района фразу для клиента,
    // здесь — только отметка, и кнопка становится «Доставил».
    if(tail === '/arrived'){
      if(!o.arrived_at){ o.arrived_at = iso(now()); save(); }
      return {ok: true, arrived_at: o.arrived_at, sent: 1};
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
  const mm = p.match(/^\/api\/driver\/move\/([^/]+)\/(start|scan|accept|receive|claim|release)$/);
  if(mm){
    const what = mm[2];
    if(what === 'start'){
      const v = outView();
      if(v.status !== 'wait' && v.status !== 'live') return {ok: false, error: 'given', task: v};
      S.mvOut.giver = ME.name; save();
      return {ok: true, task: outView()};
    }
    if(what === 'accept'){
      simIn();
      const pr = S.mvIn.pairs[body.from];
      if(!pr) return {ok: false, error: 'gone'};
      const v = inView(body.from);
      if(v.status === 'done' || v.status === 'diff') return {ok: true, already: true, task: v};
      if(v.status !== 'given') return {ok: false, error: 'not_given', task: v};
      // Как на сервере с 19 сен: «Принял» — только отсканировав всё; «не всё
      // пришло» — по сканам получателя, числа руками не берутся.
      if(body.ok === false){
        const diff = v.lines.filter(l => l.recv !== l.got)
          .map(l => ({id: l.id, name: l.name, unit: l.unit, sent: l.got, got: l.recv}));
        const note = String(body.note || '').trim();
        if(!diff.length && !note) return {ok: false, error: 'diff_empty', task: v};
        pr.acc = {ok: false, lines: diff, note: note};
      }else{
        if(v.recv_left > 0) return {ok: false, error: 'scan_all', task: v};
        pr.acc = {ok: true};
      }
      save();
      return {ok: true, task: inView(body.from)};
    }
    if(what === 'receive'){
      // Скан получателя: код демонстрационный — DEMO-<позиция>-<номер>.
      simIn();
      const pr = S.mvIn.pairs[body.from];
      if(!pr) return {ok: false, verdict: 'gone'};
      const v = inView(body.from);
      if(v.status === 'done' || v.status === 'diff') return {ok: false, verdict: 'accepted', task: v};
      if(v.status !== 'given') return {ok: false, verdict: 'not_given', task: v, from_code: v.from_code};
      const pid = String(body.code || '').split('-')[1] || '';
      const l = pr.lines.find(x => x.id === pid && (+x.recv || 0) < x.got);
      if(!l){
        const any = pr.lines.find(x => x.id === pid);
        return {ok: false, verdict: any ? 'full' : 'not_in_transfer', code: body.code, name: (P[pid] || {}).name || ''};
      }
      l.recv = num((+l.recv || 0) + 1);
      let t = inView(body.from);
      const finished = t.recv_left <= 0;
      if(finished){ pr.acc = {ok: true}; t = inView(body.from); }
      save();
      return {ok: true, code: body.code, name: l.name, qty: 1, unit: 1,
              line: t.lines.find(x => x.id === pid), task: t, finished: finished, task_done: finished};
    }
    if(what === 'scan'){
      // Скан отдающего: код демонстрационный, товар в нём зашит — DEMO-<позиция>-<номер>.
      const o = S.mvOut;
      const pid = String(body.code || '').split('-')[1] || '';
      const l = o.lines.find(x => x.id === pid && x.got < x.qty);
      if(!l){
        const any = o.lines.find(x => x.id === pid);
        return {ok: false, verdict: any ? 'full' : 'not_in_task', code: body.code, name: (P[pid] || {}).name || ''};
      }
      if(!o.giver) o.giver = ME.name;
      l.got = num(l.got + 1);
      save();
      const v = outView();
      return {ok: true, code: body.code, name: l.name, qty: 1, unit: 1, from_code: DCODE[ME.district],
              to_code: DCODE[o.to], line: v.lines.find(x => x.id === pid), task: v, finished: v.left <= 0};
    }
    return {ok: true};                   // «Взять» / «Отпустить» — старый порядок, ни на что не влияет
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
      if(body.pay) old.pay = body.pay;
      old.status = 'pending'; old.at_ms = now();
    }else{
      S.exp.push({id: 'e' + (++S.seq), kind: body.kind, kind_t: KIND_T[body.kind] || 'Расход',
                  amount: +body.amount || 0, comment: body.comment || '', status: 'pending',
                  photo: body.photo ? 'p' : '', thumb: body.thumb || '', car_photo: body.car_photo ? 'p' : '',
                  plus: !!(KINDS.find(k => k.id === body.kind) || {}).plus, at: iso(now()), at_ms: now(),
                  ...(body.pay ? {pay: body.pay} : {})});
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

  /* ── ревизия района ── */
  if(p === '/api/driver/audit/brief')
    return {state: S.aud.state, district_code: ME.district_code, district_name: ME.district_name,
            scans: S.aud.state === 'running' ? Object.keys(S.aud.codes).length : 0};
  if(p === '/api/driver/audit' && m === 'GET') return audFull();
  if(p === '/api/driver/audit/start'){
    if(S.aud.finished_at) return {ok: false, error: 'finished', ...audFull()};
    if(S.aud.state !== 'running'){
      S.aud.state = 'running'; S.aud.started_at = now(); S.aud.started_by = ME.name; save();
    }
    return {ok: true, ...audFull()};
  }
  if(p === '/api/driver/audit/scan'){
    const code = String(body.code || '');
    if(!code) return {ok: false, error: 'empty_code'};
    if(S.aud.finished_at) return {ok: false, error: 'finished'};
    if(S.aud.state !== 'running'){
      S.aud.state = 'running'; S.aud.started_at = now(); S.aud.started_by = ME.name;
    }
    const pid = code.split('-')[1] || 'p1';
    const fresh_ = !S.aud.codes[code];
    if(fresh_){ S.aud.codes[code] = pid; save(); }
    const our = audOur(pid);
    return {ok: true, new: fresh_, code: code, verdict: our ? 'ok' : 'other',
            product_id: pid, name: P[pid] ? P[pid].name : 'Бутылка',
            label: (our ? ME.district_code : DCODE[AUD_HOME]) + '-' + (1000 + Object.keys(S.aud.codes).length),
            home: our ? ME.district : AUD_HOME,
            home_code: our ? ME.district_code : DCODE[AUD_HOME],
            count: audCount(pid), unit: (AUD_SHELF.find(r => r.id === pid) || {}).unit || 1,
            total: num(Object.keys(S.aud.codes).length), positions: audRows().filter(r => r.actual).length};
  }
  if(p === '/api/driver/audit/undo'){
    if(S.aud.finished_at) return {ok: false, error: 'finished'};
    const code = String(body.code || '');
    if(S.aud.codes[code]){ delete S.aud.codes[code]; save(); }
    return {ok: true, code: code, total: Object.keys(S.aud.codes).length,
            positions: audRows().filter(r => r.actual).length};
  }
  if(p === '/api/driver/audit/finish'){
    if(S.aud.finished_at) return {ok: false, error: 'finished', ...audFull()};
    S.aud.state = 'pending'; S.aud.finished_at = now(); S.aud.finished_by = ME.name;
    S.aud.note = String(body.note || '').slice(0, 300);
    save();
    return {ok: true, ...audFull()};
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
          bonus_month: 0, bonus_once: 0, advance: 0, loan: 0, advances: [],
          // Следующий месяц — как у боевого (finance_routes.person_card): оклад без удержаний.
          next: {month: (function(){ const y = +mon.slice(0, 4), m = +mon.slice(5, 7);
                   return m === 12 ? (y + 1) + '-01' : y + '-' + String(m + 1).padStart(2, '0'); })(),
                 accrued: 5000, plus: 0, bonus_month: 0, advance: 0, fines: 0, holds: 0, to_pay: 5000, unit: 'month', advances: []},
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
  if(id === 'mvV'){
    // Принимаем: бутылка той передачи, что принимаем сейчас (следующая
    // неотсканированная позиция).
    try{
      if(typeof MV !== 'undefined' && MV && MV.task && MV.task.recv){
        const pr = S.mvIn.pairs[MV.task.from];
        const l = pr && pr.lines.find(x => (+x.recv || 0) < x.got);
        return l ? one(l.id) : null;
      }
    }catch(e){}
    const l = S.mvOut.lines.find(x => x.got < x.qty);  // отдаём
    return l ? one(l.id) : one('p6');
  }
  if(id === 'audVid'){
    // Ревизия: бутылки полки по порядку, каждая — один раз за проход.
    const шли = {};
    Object.keys(S.aud.codes).forEach(function(c){
      const pid = S.aud.codes[c]; шли[pid] = (шли[pid] || 0) + 1;
    });
    for(let i = 0; i < AUD_SEES.length; i++){
      const pid = AUD_SEES[i];
      const было = AUD_SEES.slice(0, i).filter(x => x === pid).length;
      if((шли[pid] || 0) <= было) return one(pid);
    }
    return null;                                      // полка кончилась
  }
  return one('p1');                                   // бой и бутылка охране
}
// Геопозиция в демо своя: у телефона её не спрашиваем (разрешение ради
// показа — лишнее), а без ответа не открыть смену. Подменяем две функции
// приложения, а не телеграм: в телеграме его LocationManager не перезаписать.
function patchGeo(){
  window.geoReady = async function(){ return LM; };
  window.geoRead = async function(){
    return {lat: DEMO_POINT.latitude, lon: DEMO_POINT.longitude,
            acc: DEMO_POINT.horizontal_accuracy};
  };
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
    + '<li>«Товар» — приёмка и перемещение между районами: отдаёте — сканируете, принимаете — тоже сканируете каждую бутылку. Сканер работает сам, камеру наводить не нужно.</li>'
    + '<li>«Ревизия» внизу «Товара» — пересчёт своего района по кодам: что не нашли и что лишнее, отчёт уходит старшему.</li>'
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
  patchGeo();
  try{ window.AmbarCall = null; }catch(e){}        // звонок без сервера не поднять
  // Подтягивать снимок чека с сервера демо незачем: свой кадр у него и так под
  // рукой, а ручки за окном вкладки нет.
  window.xpSharpen = function(){};
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
