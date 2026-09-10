"""Стенд водителя из НАСТОЯЩЕГО driver/index.html: телеграм и api.js подменены
заглушками с фикстурами, boot() → standBoot(). Параметры: ?fx=1 (заказ с
оплатой в валюте), ?req=edit|cancel (открытая просьба), ?chat=1 (ответ
оператора), ?w=390. python3 tools/drv_stand_gen.py <dir> → <dir>/drv.html"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
src = open(os.path.join(ROOT, "driver", "index.html"), encoding="utf-8").read()

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
  if(path === '/api/driver/orders') return {day: '2026-09-11', active: [ST_ORDER, ST_ORDER2], done: [], total_aed: 0, panic: false};
  if(path === '/api/driver/expenses') return {day: '2026-09-11', drivers: [], totals: {}, held: [], held_total: 0};
  if(path === '/api/driver/supply') return {mine: [], free: [], extra: [], taken: []};
  if(path === '/api/driver/shift') return ST_SHIFT;
  if(path === '/api/driver/rates') return ST_RATES;
  if(path === '/api/driver/catalog') return ST_CAT;
  if(path.endsWith('/chat') && m === 'GET') return {order_id: ST_ORDER.order_id, chat: [{by:'driver', name:'Али', text:'Клиент не отвечает', at:_agoIso(4), kind:'client'}, {by:'operator', name:'Парвиз', text:'Ждите 5 минут, звоню клиенту', at:_agoIso(2), kind:''}], operator: 'Парвиз'};
  stLog('API ' + m + ' ' + path + ' ' + JSON.stringify(opts.body || {}));
  if(path.endsWith('/fx')) { const r = ST_RATES.rates.find(x => x.code === (opts.body || {}).code); ST_ORDER.pay_fx = r ? {code: r.code, name: r.name, sym: r.sym, rate: r.rate, amount: Math.round(ST_ORDER.total / r.rate * 100) / 100} : null; return {ok: true, pay_fx: ST_ORDER.pay_fx}; }
  return {ok: true};
}};
const ST_SHIFT = {day: '2026-09-11', working: true, opened: true, opened_at: _agoIso(120), closed: false, closed_at: '', geo: {ok: true, fresh: true, stream: true, watch_ok: true, lost: false, still_sec: 60, age_sec: 30, endless: true, left_min: 0}, must: [], must_names: [], in_route: [], can_open: false, can_close: true, geo_bot: ''};
async function standBoot(){
  ME = {name: 'Али', district: 'alg', district_code: 'B4'};
  try{ document.getElementById('dAv').textContent = 'АЛ'; document.getElementById('dName').textContent = 'Али'; }catch(e){}
  SH = ST_SHIFT;
  await load();
  setTab(ST_Q.get('tab') || 'orders');
  await new Promise(r => setTimeout(r, 80));
  if(ST_Q.get('open') === 'fx') fxOpen(ST_ORDER.order_id);
  if(ST_Q.get('open') === 'edit'){ openEdit(ST_ORDER.order_id); await pickOpen(); if(ST_Q.get('pick')) pickStep('gin', 0, 1); }
  if(ST_Q.get('open') === 'stl') stlOpen(ST_ORDER.order_id);
  if(ST_Q.get('open') === 'chat') caseOpen(ST_ORDER.order_id);
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
src = src.replace("</head>", """<style>html,body{height:auto;min-height:0}body{width:390px;margin:0;overflow:visible}.sheet{right:auto;width:390px}.sheet-in{max-width:390px}#sterr{white-space:pre-wrap;font:11px/1.3 monospace;color:#f88;padding:10px;width:390px}</style></head>""", 1)
src = src.replace("</body>", '<div id="sterr"></div></body>', 1)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "drv.html"), "w", encoding="utf-8").write(src)
print("drv.html written")
