"""Стенд водителя из НАСТОЯЩЕГО driver/index.html: телеграм и api.js подменены
заглушками с фикстурами, boot() → standBoot(). Параметры: ?fx=1 (заказ с
оплатой в валюте), ?req=edit|cancel (открытая просьба), ?chat=1 (ответ
оператора), ?w=390. python3 tools/drv_stand_gen.py <dir> → <dir>/drv.html"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "driver", "index.html"), encoding="utf-8").read()
# картинки приложения — рядом со стендом, пути в разметке относительные
import shutil
_img = os.path.join(ROOT, "driver", "img")
if os.path.isdir(_img):
    shutil.copytree(_img, os.path.join(OUT, "img"), dirs_exist_ok=True)

TG = """<script>
window.Telegram = {WebApp: new Proxy({initData: 'x', version: '8.0', platform: 'ios', colorScheme: 'dark',
  themeParams: {}, viewportHeight: 800, viewportStableHeight: 800, isExpanded: true,
  HapticFeedback: {impactOccurred(){}, notificationOccurred(){}, selectionChanged(){}},
  BackButton: {show(){}, hide(){}, onClick(){}, offClick(){}}, MainButton: {hide(){}, show(){}, setText(){}, onClick(){}},
  LocationManager: null, ready(){}, expand(){}, onEvent(){}, offEvent(){}, close(){}, showConfirm(t, cb){ cb(true); },
  showAlert(t, cb){ cb && cb(); }, openLink(){}, openTelegramLink(){}, setHeaderColor(){}, setBackgroundColor(){},
  enableClosingConfirmation(){}, disableVerticalSwipes(){}, requestFullscreen(){}, initDataUnsafe: {user: {id: 1, first_name: 'Али'}}},
  {get(t, k){ return k in t ? t[k] : (() => {}); }})};
</script>"""
assert src.count('<script src="/vendor/telegram-web-app.js"></script>') == 1
src = src.replace('<script src="/vendor/telegram-web-app.js"></script>', TG)
src = src.replace("""<script>if(!window.Telegram||!window.Telegram.WebApp){document.write('<scr'+'ipt src="https://telegram.org/js/telegram-web-app.js"><\\/scr'+'ipt>');}</script>""", "")

API = r"""<script>
const ST_Q = new URLSearchParams(location.search);
const ST_SUM = {day:'2026-09-14', opened_at:'2026-09-14T08:12:00+04:00', on_hand:1834, cash_taken:2050, spent:246, got:30,
  tips:120, tips_cash:100, tips_other:20, tips_by:[{who:'Али', aed:80}, {who:'Фарух', aed:40}],
  orders:9, gross:3155, pay:{cash:{n:6, aed:2050}, app:{n:2, aed:705}, crypto:{n:1, aed:400}, debt:{n:0, aed:0}, free:{n:1, aed:95}},
  expenses:[{id:'fuel', t:'Заправка', plus:false, aed:150, n:1}, {id:'parking', t:'Парковка', plus:false, aed:36, n:2},
            {id:'guard', t:'Охрана', plus:false, aed:60, n:1}, {id:'we_got', t:'Нам вернули', plus:true, aed:30, n:1}],
  exp_n:5, exp_pending:2, writeoffs:1, writeoff_qty:1,
  // Две пачки и наличные на руках — как их считает cash_math на сервере
  // (эти же числа показывает карточка «Наличные за смену» на «Расходах»).
  hand:{taken:2050, taken_aed:1945, orders_cash:6,
        fx:[{code:'USD', sym:'$', amount:30, aed:105}],
        tea:120, tea_other:20, tea_by:[{who:'Али', aed:80}, {who:'Фарух', aed:40}],
        spent:[{t:'Заправка', aed:150}, {t:'Парковка', aed:36}, {t:'Охрана', aed:60}], spent_sum:246,
        got:30, got_list:[{t:'Нам вернули', aed:30}], meal:80, bonus:15, card_spent:0, card_got:0, pending:2,
        revenue:1619, revenue_aed:1514, keep:95, in_hand:1834}};
// &debt=1 — в смене был заказ в долг: товар уехал, денег за него нет.
if(ST_Q.get('debt')){
  ST_SUM.pay.debt = {n:1, aed:250};
  Object.assign(ST_SUM.hand, {debt:250, debt_n:1, debt_list:[{id:'AMB82300EB5', who:'Ахмед', aed:250}]});
}
const ST_LOG = []; function stLog(m){ ST_LOG.push(m); const e = document.getElementById('sterr'); if(e) e.textContent = ST_LOG.join('\n'); }
window.onerror = (m, s, l, c) => stLog('ERROR ' + m + ' @' + l + ':' + c);
const ST_NOW = new Date();
const _agoIso = mins => new Date(ST_NOW.getTime() - mins * 60000).toISOString().replace('Z', '');
const ST_ORDER = {
  order_id: 'AMB82300EB5', status: 'approved', address: 'Ноябрьская улица 12А', gmap_link: 'https://maps', location: {lat: 25.1, lon: 55.1},
  district: 'Алгусес', customer_name: 'Клиент', source: ST_Q.get('src') || 'app', tip: 0,
  items: [{id: 'absolut', name: 'Absolut 1 ltr', qty: 1, pcs: null, price: 95, line_total: 95, gift: false},
          {id: 'gin', name: "Gordon's London Dry 0.7", qty: 2, pcs: null, price: 100, line_total: 200, gift: false}],
  total: 295, comment: 'ТЕСТ', payment_method: '', prepaid: false, timestamp: _agoIso(5), confirmed_at: _agoIso(1),
  delivered_at: '', deliver_by: '03:49', eta: 30, driver_ack_at: 'x', driver_req: null, chat_n: 1, chat_new: 0, chat_at: '',
  settle: null, owed: 0, pay_fx: null, chat_last: {text: 'Ждите 5 минут, звоню клиенту', at: _agoIso(2), name: 'Парвиз'}};
if(ST_Q.get('fx')) ST_ORDER.pay_fx = {code: 'USD', name: 'Доллар США', sym: '$', rate: 3.67, amount: 80.38, at: ''};
if(ST_Q.get('req')) ST_ORDER.driver_req = {kind: ST_Q.get('req'), status: 'open', text: ST_Q.get('req') === 'cancel' ? 'Клиент отказался' : '',
  diff: ST_Q.get('req') === 'edit' ? [{kind: 'add', name: 'Beluga', qty: 1}] : [], total: 400, at: _agoIso(1)};
if(ST_Q.get('chat')) ST_ORDER.chat_new = 1;
if(ST_Q.get('stl')) ST_ORDER.settle = {taken: 110.1, diff: 10.1, by: 'Али', at: '', fx: {code: 'USD', amount: 30, rate: 3.67, sym: '$'}};
const ST_ORDER2 = {...ST_ORDER, order_id: 'AMB00000002', address: 'Marina Walk 7', items: [ST_ORDER.items[0]], total: 95, deliver_by: '04:20', pay_fx: null, driver_req: null, chat_new: 0};
let ST_ACTIVE = [ST_ORDER, ST_ORDER2];   // список заказов в работе; mock=1 добавляет третий
const ST_RATES = {rates: [{code:'USD', name:'Доллар США', sym:'$', rate:3.67, cash:true, main:true}, {code:'EUR', name:'Евро', sym:'€', rate:4.02, cash:true, main:true},
  {code:'GBP', name:'Фунт стерлингов', sym:'£', rate:4.65, cash:false, main:true}, {code:'RUB', name:'Российский рубль', sym:'₽', rate:0.0405, cash:true, main:true},
  {code:'TRY', name:'Турецкая лира', sym:'₺', rate:0.089, cash:false, main:true}, {code:'CNY', name:'Китайский юань', sym:'¥', rate:0.51, cash:false, main:true},
  {code:'KZT', name:'Казахстанский тенге', sym:'₸', rate:0.0069, cash:false, main:false}, {code:'INR', name:'Индийская рупия', sym:'₹', rate:0.0415, cash:false, main:false}], at: 't', ok: true};
const ST_CAT = {items: [
  {id:'absolut', name:'Absolut 1 ltr', cat:'Водка', price:100, app:95, pack:false},
  {id:'gin', name:"Gordon's London Dry 0.7", cat:'Джин', price:105, app:100, pack:false},
  {id:'beer', name:'Heineken 0.33', cat:'Пиво', price:12, app:11, pack:true, p12:140, p24:275, app12:135, app24:265},
  {id:'jd', name:"Jack Daniel's 1 ltr", cat:'Виски', price:180, app:170, pack:false}], cats: ['Водка','Джин','Пиво','Виски']};
window.drvApi = {AMBAR_API: '', drvFetch: async (path, opts = {}) => {
  const m = (opts.method || 'GET');
  // Проверка «пока грузится»: ?slow=мс задерживает ответы, ?fail=profile|rates роняет их
  const _sl = +(ST_Q.get('slow') || 0);
  if(_sl && /\/(profile|rates)$/.test(path)) await new Promise(r => setTimeout(r, _sl));
  const _f = ST_Q.get('fail') || '';
  if(_f && path.indexOf('/api/driver/' + _f) === 0){
    const lim = ST_Q.get('failn');                // ?failn=1 — сорвать только первые N, дальше как обычно
    window.__FAILN = (window.__FAILN || 0) + 1;
    if(!lim || window.__FAILN <= +lim){ const e = new Error('stand fail'); e.status = +(ST_Q.get('code') || 500); throw e; }
  }
  const ST_DISTR = [{id:'alg', code:'B1', name:'Алгусес'}, {id:'jvc', code:'B2', name:'JVC'}, {id:'dm', code:'B3', name:'Дубай Марина'}, {id:'bbay', code:'B4', name:'Бизнес Бей'}, {id:'silicon', code:'B5', name:'Силикон Оазис'}];
  if(path === '/api/driver/stock/moves') return {day: '2026-09-14', rows: [], districts: ST_DISTR};
  if(path.indexOf('/api/driver/stock/code') === 0) return ST_Q.get('badcode')
    ? {ok: false, verdict: 'written', say: 'была списана', code: 'AMB-0001-Q', label: 'Q-0001', name: 'Absolut 1 ltr', img: 'https://ambar-delivery.com/products/p1.webp', cat: 'Водка', price: 95, district: {id:'dm', code:'B3', name:'Дубай Марина'}, at: _agoIso(3*24*60), moves: 0, last_move: null, districts: ST_DISTR}
    : {ok: true, verdict: 'ok', say: '', code: 'AMB-0001-Q', label: 'Q-0001', name: 'Absolut 1 ltr', img: 'https://ambar-delivery.com/products/p1.webp', cat: 'Водка', price: 95, district: {id:'dm', code:'B3', name:'Дубай Марина'}, at: _agoIso(3*24*60), moves: 1, last_move: {from_code:'B1', to_code:'B3', at: _agoIso(60)}, districts: ST_DISTR};
  if(path === '/api/driver/stock/move' && m === 'POST') return {ok: true, verdict: 'ok', name: 'Absolut 1 ltr', from_code: 'B3', to_code: 'B1'};
  if(path === '/api/driver/orders') return {day: '2026-09-11', active: (ST_Q.get('noorders') || ST_Q.get('tab') === 'shift' && !ST_Q.get('route')) ? [] : ST_ACTIVE, done: [{...ST_ORDER2, order_id: 'AMB00000009', delivered_at: _agoIso(38)}], total_aed: 95, panic: false, fx_take: [{code:'USD', name:'Доллар США', sym:'$', rate:3.5}, {code:'EUR', name:'Евро', sym:'€', rate:4}, {code:'GBP', name:'Фунт стерлингов', sym:'£', rate:4.5}, {code:'SAR', name:'Риал', sym:'SAR', rate:1}]};
  if(path === '/api/driver/expenses' && m === 'POST'){ stLog('API POST ' + path + ' ' + JSON.stringify(opts.body || {})); return {ok: true}; }
  if(path === '/api/driver/expenses') return {day: '2026-09-11', drivers: [], totals: {}, held: [], held_total: 0, working: true, meal_rates: {working: 80, off: 40},
    kinds: [{id:'fuel',t:'Заправка',receipt:true},{id:'wash',t:'Мойка',receipt:true},{id:'parking',t:'Парковка',receipt:true},{id:'guard',t:'Охрана'},{id:'kfc',t:'KFC · премия'},{id:'we_gave',t:'Мы вернули'},{id:'owed_us',t:'Нам должны'},{id:'we_got',t:'Нам вернули',plus:true},{id:'we_owe',t:'Мы должны',plus:true},{id:'advance',t:'Аванс зарплаты',pay:false},{id:'advance_bonus',t:'Аванс премии',pay:false},{id:'other',t:'Что-то ещё',receipt_opt:true}],
    // &expdone=1 — как на макете «Основные расходы»: питание 80, бензин 2 AED, мойки и парковки не было;
    // &exp2=1 — второй макет: бензин 2 AED, мойка и парковка не заполнены (2/4)
    extras: ((ST_Q.get('expdone') || ST_Q.get('exp2')) ? [{id:'x1', kind:'fuel', amount:2, status:'approved', photo:'p', comment:''}]
      : ST_Q.get('expfull') ? [{id:'x1', kind:'fuel', amount:120, status:'approved', photo:'p', comment:''}, {id:'x2', kind:'parking', kind_t:'Парковка', amount:25, status:'pending', comment:'Marina Mall'}] : [])
      .concat(ST_Q.get('debt') ? [{id:'x9', kind:'owed_us', kind_t:'Нам должны', amount:250, status:'pending',
        nocash:true, auto:true, order:'AMB82300EB5', comment:'#AMB82300EB5 · Ахмед · заказ в долг'}] : []),
    by_kind: (ST_Q.get('expdone') || ST_Q.get('exp2')) ? {fuel:{sum:2,count:1}} : ST_Q.get('expfull') ? {fuel:{sum:120,count:1}, parking:{sum:25,count:1}} : {},
    no_expense: (ST_Q.get('must') || ST_Q.get('exp2')) ? {} : (ST_Q.get('expdone') ? {wash: true, parking: true} : ST_Q.get('expfull') ? {wash: true} : {fuel: true, wash: true, parking: true}), pending_answer: ST_Q.get('must') ? ['fuel', 'wash', 'parking'] : ST_Q.get('exp2') ? ['wash', 'parking'] : []};
  // &supdyn=1 — район B1 свободен, на 3–4 опросе его берёт другой, потом отдаёт:
  // проверка, что вкладка «Товар» перерисовывается сама, без перезапуска
  // &supx=1 — заявка на другую базу (Al Hamra Cellar), моя задача B1 из трёх
  // позиций без цен (&priced=1 — цены уже вписаны); буй/hold/noscan подменены
  if(ST_Q.get('supx')){
    window.__buys = window.__buys || (ST_Q.get('priced') ? {absolut: 62, gin: 55, beer: 96} : {});
    const __xTask = () => {
      const L = [{id:'absolut', name:'Absolut 1 ltr', need:12, qty_total:24, unit_n:1, unit_name:'бутылку'},
                 {id:'gin', name:"Gordon's London Dry 0.7", need:6, qty_total:6, unit_n:1, unit_name:'бутылку'},
                 {id:'beer', name:'Heineken 0.33', need:48, qty_total:96, unit_n:24, unit_name:'ящик'}]
        .map(l => { const p = +(window.__buys[l.id] || 0); return {...l, got: 0, left: l.need, price: p, units: Math.max(1, Math.round(l.qty_total / l.unit_n))}; });
      const cost = L.reduce((a, l) => a + (l.price > 0 ? l.price * l.units : 0), 0);
      const need = L.reduce((a, l) => a + l.need, 0);
      return {supply_id: 's2', at: _agoIso(40), day: '2026-09-14', district: 'jvc', district_code: 'B1', district_name: 'JVC',
        driver: 'Али', mine: true, extra: true, base: 'Al Hamra Cellar', claimed_at: _agoIso(30), started_at: '', done_at: '',
        locked: false, lock_at: '', erev: 0, noscan_at: '', noscan_by: '', cancelled_at: '', cancelled_by: '', note: '', gaps: [],
        need, got: 0, left: need, positions: L.length, lines: L, hold: {who: '', kind: '', live: false, mine: false},
        prices_ok: L.every(l => l.price > 0), cost: Math.round(cost)};
    };
    if(path === '/api/driver/supply') return {mine: [__xTask()], free: [], extra: [], taken: []};
    if(path === '/api/driver/supply/s2' && m === 'GET') return __xTask();
    if(path === '/api/driver/supply/s2/buy'){ const b = opts.body || {}; if(+b.price > 0) window.__buys[b.product_id] = +b.price; else delete window.__buys[b.product_id];
      stLog('API POST buy ' + JSON.stringify(b)); return __xTask(); }
    if(path === '/api/driver/supply/s2/hold') return {ok: true, sec: 25, hold: {who: 'Али', kind: 'driver', live: false, mine: true}};
    if(path === '/api/driver/supply/s2/noscan'){ const t = __xTask(); stLog('API POST noscan');
      return t.prices_ok ? {ok: true, ...t, noscan_at: _agoIso(0)} : {ok: false, verdict: 'prices_needed', task: t}; }
    if(path === '/api/driver/supply/s2/release') return {ok: true};
  }
  // &suphist=1 — история приёмок: две закрытые задачи (доп. база с ценами и основная без сканирования)
  if(ST_Q.get('suphist')){
    const H = {
      s3: {supply_id: 's3', district: 'jvc', district_code: 'B1', district_name: 'JVC', extra: true, base: 'Al Hamra Cellar', day: '2026-09-13',
           done_at: _agoIso(600), noscan_at: '', need: 66, got: 66, positions: 3, cost: 2202, prices_ok: true,
           lines: [{id:'absolut', name:'Absolut 1 ltr', need:12, got:12, left:0, price:62, unit_name:'бутылку'}, {id:'gin', name:"Gordon's London Dry 0.7", need:6, got:6, left:0, price:55, unit_name:'бутылку'}, {id:'beer', name:'Heineken 0.33', need:48, got:48, left:0, price:96, unit_name:'ящик'}]},
      s4: {supply_id: 's4', district: 'tecom', district_code: 'B2', district_name: 'Теком', extra: false, base: '', day: '2026-09-12',
           done_at: _agoIso(2000), noscan_at: _agoIso(2100), need: 48, got: 40, positions: 2,
           lines: [{id:'absolut', name:'Absolut 1 ltr', need:24, got:24, left:0}, {id:'jd', name:"Jack Daniel's 1 ltr", need:24, got:16, left:8}]}};
    const full = k => ({...H[k], at: _agoIso(3000), driver: 'Али', mine: true, claimed_at: _agoIso(2900), started_at: _agoIso(2800),
      locked: true, lock_at: '', erev: 0, noscan_by: '', cancelled_at: '', cancelled_by: '', note: '', gaps: [], hold: {who:'', kind:'', live:false, mine:false}});
    if(path === '/api/driver/supply/history') return {rows: [H.s3, H.s4].map(r => ({...r, lines: undefined}))};
    if(path === '/api/driver/supply/s3' && m === 'GET') return full('s3');
    if(path === '/api/driver/supply/s4' && m === 'GET') return full('s4');
  }
  if(path === '/api/driver/supply/history') return {rows: []};
  // бутылка охране по коду: 'habr' — наша, остальное — не наша
  if(path === '/api/driver/bottle') return opts.params && opts.params.code === 'habr'
    ? {ok: true, bottle: 'Absolut 1 ltr', cost: 62} : {ok: false, say: 'не наша бутылка'};
  if(path === '/api/driver/supply' && ST_Q.get('supdyn')){
    window.__supN = (window.__supN || 0) + 1;
    const t = {supply_id: 's1', district: 'jvc', district_code: 'B1', district_name: 'JVC', need: 24, got: 0,
               lines: [{id: 'absolut', name: 'Absolut 1 ltr', need: 24, got: 0, left: 24}], mine: false, extra: false,
               base: '', hold: {who: '', kind: '', live: false, mine: false}, noscan_at: null, stale: false, at: _agoIso(3)};
    const taken = window.__supN >= 3 && window.__supN <= 4;
    stLog('SUP poll ' + window.__supN + (taken ? ' taken' : ' free'));
    return taken ? {mine: [], free: [], extra: [], taken: [{...t, driver: 'Фарух'}]} : {mine: [], free: [t], extra: [], taken: []};
  }
  if(path === '/api/driver/supply') return {mine: [], free: [], extra: [], taken: []};
  if(path.split('?')[0] === '/api/driver/shift/summary') return {...ST_SUM, ...(ST_SHIFT.after_close ? {closed_at: ST_SHIFT.report_closed_at} : {})};   // ?day= — просмотр закрытой
  if(path === '/api/driver/shift/close' && m === 'POST'){ stLog('API POST ' + path); const at = new Date().toISOString(); return {...ST_SHIFT, closed: true, closed_at: at, after_close: true, report_day: ST_SHIFT.day, report_closed_at: at, can_close: false, can_open: false, in_route: [], must: []}; }
  if(path === '/api/driver/shift') return ST_SHIFT;
  if(path === '/api/driver/history') return ST_Q.get('histempty') ? {ok: true, today: '2026-09-13', days: []} : ST_HIST;
  if(path === '/api/driver/rates') return ST_FX;
  if(path === '/api/driver/catalog') return ST_CAT;
  if(path === '/api/driver/profile') return ST_PROF;
  if(path.endsWith('/chat') && m === 'GET' && ST_Q.get('nochat')) return {order_id: ST_ORDER.order_id, chat: []};   // ?nochat=1 — пустой разговор
  if(path.endsWith('/chat') && m === 'GET') return {order_id: ST_ORDER.order_id, chat: [{by:'driver', name:'Али', text:'Клиент не отвечает', at:_agoIso(4), kind:'client'}, {by:'operator', name:'Парвиз', text:'Ждите 5 минут, звоню клиенту', at:_agoIso(2), kind:''}], operator: 'Парвиз'};
  stLog('API ' + m + ' ' + path + ' ' + JSON.stringify(opts.body || {}));
  if(path.endsWith('/fx')) { const r = ST_RATES.rates.find(x => x.code === (opts.body || {}).code); ST_ORDER.pay_fx = r ? {code: r.code, name: r.name, sym: r.sym, rate: r.rate, amount: Math.round(ST_ORDER.total / r.rate * 100) / 100} : null; return {ok: true, pay_fx: ST_ORDER.pay_fx}; }
  return {ok: true};
}};
// история заказов: вчера было три заказа, сегодня пусто
const ST_HIST = {ok: true, today: '2026-09-13', days: [
  {day: '2026-09-12', count: 3, aed: 640, cash: 440, online: 200, avg_min: 23, orders: [
    {order_id: 'AMB00000011', address: 'Dubai Marina', total: 295, delivered_at: '2026-09-12T12:24:00', timestamp: '2026-09-12T11:50:00', items: [{id:'absolut', name:'Absolut 1 ltr', qty: 2, price: 95}], mins: 24, pay_fx: null, prepaid: true, payment_method: 'card', district: 'Marina'},
    {order_id: 'AMB00000012', address: 'Business Bay', total: 170, delivered_at: '2026-09-12T11:48:00', timestamp: '2026-09-12T11:20:00', items: [{id:'gin', name:"Gordon's London Dry 0.7", qty: 1, price: 100}], mins: 18, pay_fx: null, prepaid: false, payment_method: 'cash', district: 'Business Bay'},
    {order_id: 'AMB00000013', address: 'Deira City Centre', total: 175, delivered_at: '2026-09-12T10:32:00', timestamp: '2026-09-12T10:02:00', items: [{id:'jd', name:"Jack Daniel's 1 ltr", qty: 1, price: 180}], mins: 27, pay_fx: null, prepaid: true, payment_method: 'card', district: 'Deira'}]}]};
// профиль: зарплата и списания за месяц
const ST_FX = {ok: true, at: new Date(Date.now() - 40*60000).toISOString(), silent: false, days: 6, rates: [
  {code:'USD', name:'Доллар США', sym:'$', rate:3.673, market:3.6725, cash:true, main:true, kind:'fiat', prev:3.6686, change:0.12, spark:[3.668,3.669,3.6705,3.669,3.6715,3.673]},
  {code:'EUR', name:'Евро', sym:'€', rate:4.261, market:4.258, cash:true, main:true, kind:'fiat', prev:4.2644, change:-0.08, spark:[4.272,4.269,4.2655,4.267,4.2644,4.261]},
  {code:'GBP', name:'Фунт стерлингов', sym:'£', rate:5.015, market:5.01, cash:false, main:true, kind:'fiat', prev:5.0075, change:0.15, spark:[5.0,5.004,5.002,5.009,5.0075,5.015]},
  {code:'RUB', name:'Рубль', sym:'₽', rate:0.0435, market:0.0433, cash:true, main:true, kind:'fiat', prev:0.0436, change:-0.21, spark:[0.0441,0.0439,0.0438,0.0437,0.0436,0.0435]},
  {code:'TRY', name:'Турецкая лира', sym:'₺', rate:0.0756, market:0.0755, cash:false, main:true, kind:'fiat', prev:0.0754, change:0.32, spark:[0.0749,0.0751,0.075,0.0753,0.0754,0.0756]},
  {code:'CNY', name:'Юань', sym:'¥', rate:0.5456, market:0.5454, cash:false, main:true, kind:'fiat', prev:0.5446, change:0.18, spark:[0.5432,0.5438,0.5435,0.5442,0.5446,0.5456]},
  {code:'TJS', name:'Таджикский сомони', sym:'', rate:0.3978, market:0.3978, cash:false, main:true, kind:'fiat', prev:0.3974, change:0.10, spark:[0.3969,0.3972,0.3971,0.3975,0.3974,0.3978]},
  {code:'AFN', name:'AFN', sym:'', rate:0.057, market:0.057, cash:false, main:false, kind:'fiat', prev:0.0569, change:0.17, spark:[0.0568,0.0569,0.0569,0.0569,0.0569,0.057]},
  {code:'KZT', name:'Тенге', sym:'', rate:0.0081, market:0.0081, cash:false, main:false, kind:'fiat', prev:0.0081, change:-0.03, spark:[]},
  {code:'USDT', name:'Tether USDT', sym:'', rate:3.673, market:3.673, cash:false, main:true, kind:'crypto', prev:3.6716, change:0.04, spark:[3.671,3.6705,3.672,3.6715,3.6716,3.673]}]};
// первый день: истории ещё нет — ни процентов, ни графика
if(ST_Q.get('fxnew')){ ST_FX.days = 0; ST_FX.rates.forEach(r => { r.change = null; r.prev = null; r.spark = []; }); }
const ST_PROF = {month: '2026-09', name: 'Худоба', role: 'driver', role_t: 'Водитель', code: 'B1', district: 'jvc',
  district_name: 'JVC', since: '2025-05-12', active: true, rate: 5000, cur: 'AED', unit: 'month', days: 26,
  accrued: 5000, plus: 0, minus: 1750, fines: 1350, holds: 400, to_pay: 3250, paid: 0, left: 3250, debt: 0,
  month_total: 1750, month_count: 5,
  items: [
    // решения старшего по тому, что сформировала программа (22 сен 2026): прощённое и урезанное питание
    {id: 'auto:late_shift:2026-09-13', kind: 'fine', t: 'Штраф', amount: 40, per_month: 0, day: '2026-09-13', at: '', reason: 'Поздно открыл смену', note: 'открыл в 18:57, правило — до 18:00', text: 'Открытие смены позже 18:00 — смена открыта в 18:57', due: 0, left: 0, done: false, cancelled: false, auto: 'late_shift', forgiven: true, meal: true, meal_from: 80, meal_to: 40},
    {id: 'auto:geo_off:2026-09-13', kind: 'fine', t: 'Штраф', amount: 200, per_month: 0, day: '2026-09-13', at: '', reason: 'Отключил геолокацию', note: 'выключил в 22:00', text: 'Отключение геолокации — в 22:00', due: 0, left: 0, done: false, cancelled: false, auto: 'geo_off', forgiven: true, meal: false},
    {id: 'auto:late_shift:2026-09-12', kind: 'fine', t: 'Штраф', amount: 40, per_month: 0, day: '2026-09-12', at: '', reason: 'Поздно открыл смену', note: 'открыл в 19:20, правило — до 18:00', text: 'Открытие смены позже 18:00 — смена открыта в 19:20', due: 0, left: 0, done: false, cancelled: false, auto: 'late_shift', forgiven: false, meal: true, meal_from: 80, meal_to: 40},
    {id: 'p1', kind: 'fine', t: 'Штраф', amount: 1000, per_month: 0, day: '2026-09-12', at: '2026-09-12T10:23:00', reason: 'Превышение скорости · 71 – 100 км/ч', note: 'Превышение скорости на E311', due: 1000, left: 0, done: true, cancelled: false},
    {id: 'p2', kind: 'hold', t: 'Удержание', amount: 400, per_month: 0, day: '2026-09-08', at: '2026-09-08T06:12:00', reason: '', note: 'Аванс за топливо', due: 400, left: 0, done: true, cancelled: false},
    {id: 'p3', kind: 'fine', t: 'Штраф', amount: 350, per_month: 0, day: '2026-09-05', at: '2026-09-05T12:45:00', reason: 'Использование телефона за рулём · Во время движения', note: 'Использование телефона за рулем', due: 350, left: 0, done: true, cancelled: false},
    {id: 'p4', kind: 'fine', t: 'Штраф', amount: 350, per_month: 0, day: '2026-09-01', at: '2026-09-01T05:17:00', reason: 'Неправильная парковка · В неположенном месте', note: 'Парковка в запрещённой зоне', due: 350, left: 0, done: true, cancelled: false},
    {id: 'p5', kind: 'hold', t: 'Удержание', amount: 400, per_month: 0, day: '2026-08-28', at: '2026-08-28T04:05:00', reason: 'Опоздание на смену', note: 'Опоздание более 30 минут', due: 0, left: 0, done: true, cancelled: false},
    {id: 'p6', kind: 'fine', t: 'Штраф', amount: 600, per_month: 0, day: '2026-08-20', at: '2026-08-20T08:30:00', reason: 'Неправильная парковка', note: '', due: 0, left: 0, done: false, cancelled: true},
    {id: 'p7', kind: 'fine', t: 'Штраф', amount: 1500, per_month: 0, day: '2026-08-18', at: '2026-08-18T08:30:00', reason: 'Авария по вине водителя · Повреждение автомобиля', note: '', due: 0, left: 0, done: true, cancelled: false},
    {id: 'p8', kind: 'fine', t: 'Штраф', amount: 500, per_month: 0, day: '2026-08-10', at: '2026-08-10T14:55:00', reason: 'Несоблюдение ПДД · Проезд на красный сигнал', note: '', due: 0, left: 0, done: true, cancelled: false},
    {id: 'p9', kind: 'fine', t: 'Штраф', amount: 350, per_month: 0, day: '2026-08-02', at: '2026-08-02T07:20:00', reason: 'Использование телефона за рулём · Во время движения', note: '', due: 0, left: 0, done: true, cancelled: false}]};
// ?big=1 — крупные суммы: проверка, что формула зарплаты не обрезается
if(ST_Q.get('big')){ Object.assign(ST_PROF, {accrued: 15000, fines: 12500, holds: 10000, to_pay: 11500, month_total: 22500}); }
// ?work=start|end|mid — оклад за неполный месяц: вышел 15-го, уехал 20-го, был с 5-го по 20-е
const _wk = {start: [16, ['2026-09-15', '2026-09-30']], end: [20, ['2026-09-01', '2026-09-20']], mid: [16, ['2026-09-05', '2026-09-20']]}[ST_Q.get('work')];
if(_wk){ Object.assign(ST_PROF, {name: 'Тест-водитель', work_set: true, work_days: _wk[0], month_days: 30, work_spans: [_wk[1]],
  since: _wk[1][0], active: ST_Q.get('work') === 'start',          // как на сервере: «Работает с» — день выхода
  accrued: Math.round(5000 * _wk[0] / 30), to_pay: Math.round(5000 * _wk[0] / 30) - 1750, left: Math.round(5000 * _wk[0] / 30) - 1750}); }
const ST_SHIFT = {day: '2026-09-11', working: true, opened: true, opened_at: _agoIso(134), closed: false, closed_at: '', geo: {ok: true, fresh: true, stream: true, watch_ok: true, lost: false, still_sec: 60, age_sec: 30, endless: true, left_min: 0}, must: [], must_names: [], in_route: [], can_open: false, can_close: true, geo_bot: ''};
// состояния смены для стенда: ?route=1 — заказ в пути, ?must=1 — не отвечено про расходы,
// ?shoff=1 — смена не открыта, ?shclosed=1 — закрыта, ?nogeo=1 — трансляции нет
if(ST_Q.get('route')) ST_SHIFT.in_route = ['AMB82300EB5'];
// ?intake=1 — незавершённая приёмка: смена ждёт её
if(ST_Q.get('intake')) ST_SHIFT.intake = [{sid: 'S1', district: 'jvc', code: 'JVC', name: 'JVC', need: 12, got: 7, left: 5, started: true}];
if(ST_Q.get('must')){ ST_SHIFT.must = ['fuel', 'wash']; ST_SHIFT.must_names = ['Бензин', 'Мойка']; }
if(ST_Q.get('shoff')){ ST_SHIFT.opened = false; }
// ?selfopen=1 — оператор открыл смену района, водителя не отметил: открывает сам (22 сен 2026)
if(ST_Q.get('selfopen')){ ST_SHIFT.opened = false; ST_SHIFT.working = null; ST_SHIFT.district_open = true; ST_SHIFT.late_hour = 18; ST_SHIFT.can_open = true; }
if(ST_Q.get('shclosed')){ ST_SHIFT.closed = true; ST_SHIFT.closed_at = _agoIso(5); ST_SHIFT.after_close = true; ST_SHIFT.report_day = ST_SHIFT.day; ST_SHIFT.report_closed_at = ST_SHIFT.closed_at; ST_SHIFT.can_open = false; }
// ?prevday=1 — сутки сменились, вчерашняя смена закрыта, оператор новую ещё не открыл
if(ST_Q.get('prevday')){ ST_SHIFT.opened = false; ST_SHIFT.closed = false; ST_SHIFT.after_close = true; ST_SHIFT.report_day = '2026-09-10'; ST_SHIFT.report_closed_at = '2026-09-10T22:40:00+04:00'; ST_SHIFT.can_open = false; }
if(ST_Q.get('nogeo')){ ST_SHIFT.geo.ok = false; ST_SHIFT.geo.stream = false; }
if(ST_Q.get('dayclosed')){ ST_SHIFT.day_closed = true; ST_SHIFT.day_closed_at = _agoIso(12); }
async function standBoot(){
  ME = {name: 'Али', district: 'alg', district_code: 'B4'};
  try{ document.getElementById('dAv').textContent = 'АЛ'; document.getElementById('dName').textContent = 'Али'; }catch(e){}
  SH = ST_SHIFT;
  // &mock=1 — цифры как на макетах владельца: один Absolut, 95 AED, принят
  // 00:43 (37 мин назад для карточки), доставка 50 мин, быть до 01:33, не просрочен
  if(ST_Q.get('mock')){
    const d = new Date(); d.setHours(0, 43, 0, 0);
    // mock=1: до «быть до» ~1,5 мин (красный); mock=2: ~21,5 мин (зелёный); mock=3: ~6,5 мин (жёлтый);
    // mock=4: просрочен на ~9 мин; mock=5: ~1 ч 09 мин (длинный кегль)
    const green = ST_Q.get('mock') === '2';
    const MIN = {1: 1.55, 2: 21.5, 3: 6.5, 4: -9.1, 5: 69.2}[ST_Q.get('mock')] || 1.55;
    const due = new Date(Date.now() + MIN * 60000);
    const hm = String(due.getHours()).padStart(2, '0') + ':' + String(due.getMinutes()).padStart(2, '0');
    Object.assign(ST_ORDER, {order_id: 'AMB3207975F', items: [ST_ORDER.items[0]], total: 95, eta: 50, deliver_by: hm,
      confirmed_at: new Date(Date.now() - (green ? 5 : (MIN > 30 ? 20 : 6.5)) * 60000).toISOString(), chat_n: 0});
    Object.assign(ST_ORDER2, {order_id: 'AMB3207991C', district: 'Дубай Марина', deliver_by: '02:10'});
    ST_ACTIVE.push({...ST_ORDER2, order_id: 'AMB3208003A', district: 'Джумейра', deliver_by: '02:45', address: 'Jumeirah 1'});
    if(ST_Q.get('open') === 'inc'){ ST_ORDER.confirmed_at = d.toISOString(); ST_ORDER.deliver_by = '01:33'; }
    window._isLate = () => false;
  }
  if(ST_Q.get('one')) ST_ACTIVE.length = 1;   // &one=1 — один заказ в работе (лента с одним чипом)
  await load();
  setTab(ST_Q.get('tab') || 'orders');
  shPaint();   // как в бою: boot() → shLoad() → shPaint() — замок смены/гео
  // Опрос раз в 5 с — как в бою (boot), но без проверки document.hidden:
  // панель браузера бывает скрыта, а опрос проверять надо
  setInterval(() => { if(!PANIC && !LOCKED) load(); }, 5000);
  await new Promise(r => setTimeout(r, 80));
  if(ST_Q.get('open') === 'fx') fxOpen(ST_ORDER.order_id);
  // ?open=mv — сканер перемещения; &code=1 — как будто код прочитан (камеры на стенде нет); &badcode=1 — списанная; &pick=1 — район выбран
  if(ST_Q.get('open') === 'mv'){ await mvOpen(); if(ST_Q.get('code')){ await new Promise(r => setTimeout(r, 300)); await mvCode('AMB-0001-Q'); if(ST_Q.get('pick')) mvPick('alg'); } }
  // ?open=inc — лист входящего заказа; &mock=1 — цифры как на макете владельца
  // (один Absolut, 95 AED, принят 00:43, доставка 50 мин, быть до 01:33, не просрочен)
  if(ST_Q.get('open') === 'inc') incShow(ST_ORDER);
  if(ST_Q.get('open') === 'edit'){ openEdit(ST_ORDER.order_id); await pickOpen(); if(ST_Q.get('pick')) pickStep('gin', 0, 1); }
  if(ST_Q.get('open') === 'stl') stlOpen(ST_ORDER.order_id);
  if(ST_Q.get('open') === 'chat') caseOpen(ST_ORDER.order_id);
  if(ST_Q.get('open') && ST_Q.get('open').indexOf('exp:') === 0){ await new Promise(r => setTimeout(r, 150)); expGo(ST_Q.get('open').slice(4)); }
  if(ST_Q.get('open') === 'orders'){ await new Promise(r => setTimeout(r, 150)); profOrders(); }
  // ?open=hist — история списаний в профиле; &item=<id> — сразу одна запись
  if(ST_Q.get('open') === 'hist'){ await new Promise(r => setTimeout(r, 400)); profHist(); if(ST_Q.get('item')) hsOpen(ST_Q.get('item')); }
  if(ST_Q.get('open') === 'ordhist'){ await new Promise(r => setTimeout(r, 150)); ordHist(); }   // история из ленты заказов
  if(ST_Q.get('open') === 'shs'){ await new Promise(r => setTimeout(r, 150)); shsOpen(!!(ST_Q.get('shclosed') || ST_Q.get('prevday'))); }   // итоги смены: перед закрытием или просмотр после
  // ?open=ck — «Проверить бутылки»: свободный скан. Камеры на стенде нет, и
  // смотрим ровно то, ради чего экран сделан, — ленту ответов.
  if(ST_Q.get('open') === 'ck'){
    await new Promise(r => setTimeout(r, 150));
    document.getElementById('ckScr').classList.add('show');
    document.getElementById('ckMid').innerHTML = '<div class="cam mv-cam" style="background:#000"></div>';
    CK = {busy: false, list: [
      {code: 'bud#000247', sent: 'ok', r: {verdict: 'unknown'}},
      {code: '11072', r: {verdict: 'ok', name: 'Budweiser 0.33 can', label: 'bud#000241', district_code: 'B5'}},
      {code: '90312', r: {verdict: 'written', name: 'Absolut 1 ltr', label: 'abs#000102', district_code: 'B5'}},
      {code: '77451', r: {verdict: 'sold', name: 'Chivas 12 1 ltr', label: 'chv#000018', district_code: 'B5'}},
    ]};
    ckPaint();
  }
  if(ST_Q.get('open') === 'supx'){ await new Promise(r => setTimeout(r, 300)); supOpen('s2', 'jvc'); }   // задача доп. заявки
  if(ST_Q.get('open') === 'day1'){ await new Promise(r => setTimeout(r, 150)); profOrders(); await new Promise(r => setTimeout(r, 200)); histStep(1); }
  if(ST_Q.get('open') === 'order'){ await new Promise(r => setTimeout(r, 150)); profOrders(); await new Promise(r => setTimeout(r, 200)); histStep(1); await new Promise(r => setTimeout(r, 80)); histOpen('AMB00000011'); }
  if(ST_Q.get('open') === 'pick'){ await new Promise(r => setTimeout(r, 150)); histPick(); }
  if(ST_Q.get('open') === 'hist'){ await new Promise(r => setTimeout(r, 150)); profHist(); if(ST_Q.get('chip')) hsPick(ST_Q.get('chip')); if(ST_Q.get('item')) hsOpen(ST_Q.get('item')); if(ST_Q.get('mon')) hsMonth(); }
  if(ST_Q.get('open') === 'rates'){ await new Promise(r => setTimeout(r, 150)); profRates(); if(ST_Q.get('chip')) fxdPick(ST_Q.get('chip')); if(ST_Q.get('row')) fxdOpen(ST_Q.get('row')); }
  // ?dbg=sel1,sel2 — размеры элементов в конце страницы (для промеров без браузера)
  if(ST_Q.get('dbg')){
    await new Promise(r => setTimeout(r, 200));
    const out = document.createElement('pre'); out.id = 'dbgout';
    out.textContent = ST_Q.get('dbg').split(',').map(sel => Array.from(document.querySelectorAll(sel)).slice(0, 4).map(el => {
      const r = el.getBoundingClientRect(); return `${sel} x ${r.left.toFixed(1)}-${r.right.toFixed(1)} y ${(r.top + scrollY).toFixed(1)}-${(r.bottom + scrollY).toFixed(1)} w ${r.width.toFixed(1)} h ${r.height.toFixed(1)}`;
    }).join('\n')).join('\n');
    document.body.appendChild(out);
  }
  await new Promise(r => setTimeout(r, 120));
  const card = document.querySelector('.oc');
  let over = 0;
  document.querySelectorAll('.oc-it span, .oc-addr-t, .oc .dc-cap i').forEach(el => { if(el.scrollWidth > el.clientWidth + 1) over++; });
  stLog('MEASURE w=' + document.documentElement.scrollWidth + ' card=' + (card ? Math.round(card.getBoundingClientRect().height) : 0) + ' overflow=' + over);
}
</script>"""
assert src.count('<script src="api.js"></script>') == 1
src = src.replace('<script src="api.js"></script>', API)
assert src.count("\nboot();") == 1
src = src.replace("\nboot();", "\nstandBoot();")
# Ширина экрана стенда: по умолчанию 390 (безголовый Chrome не уже 500), для
# сравнения с макетами владельца — 430: python3 tools/drv_stand_gen.py <dir> 430
W = sys.argv[2] if len(sys.argv) > 2 else '390'
src = src.replace("</head>", """<style>html,body{height:auto;min-height:0}body{width:WPX;margin:0;overflow:visible}.sheet{right:auto;width:WPX}.hdr{width:WPX}.tabbar{width:WPX}.call-bar{width:WPX}.shscr{width:WPX;right:auto}.inc{width:WPX;right:auto}.sup-scr{width:WPX;right:auto}.sheet-in{max-width:WPX}#sterr{white-space:pre-wrap;font:11px/1.3 monospace;color:#f88;padding:10px;width:WPX}</style></head>""".replace('WPX', W + 'px'), 1)
src = src.replace("</body>", '<div id="sterr"></div></body>', 1)
# для снимков журнал стенда мешает: длинные строки растягивают страницу шире 390
src = src.replace("</head>", "<script>if(new URLSearchParams(location.search).get('nolog'))document.addEventListener('DOMContentLoaded',function(){var e=document.getElementById('sterr');if(e)e.style.display='none'});</script></head>", 1)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "drv.html"), "w", encoding="utf-8").write(src)
print("drv.html written")
