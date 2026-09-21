// Модель сервера приёмки — те же правила, что supply_routes (task_scan /
// task_undo / hold), с задержками и обрывами сети. Вместо api.js обоих
// приложений: drvApi для водителя, ownerApi для STAR.
(function(){
  const APP = window.__APP = new URLSearchParams(location.search).get('app') || 'driver';
  const M = window.__M = {
    sid: 'SFZ', oid: 'jvc', me: APP === 'driver' ? 'Худоба' : 'старший', owner: APP !== 'driver',
    lines: [], reg: new Map(), seq: 0, hold: null, noscan: false, done: false, open: true, rev: 0,
    lat: [0, 0], fault: null, faultLeft: 0, faults: 0, pending: 0, log: [], viol: [],
    xtap: 0,
  };
  const NAMES = {p1: 'Absolut 1 ltr', p2: 'Stolichnaya 1 ltr', p5: 'Smirnoff Vodka 1 ltr',
                 p31: 'Heineken 0.33 can', p3: 'Russian Standard 1 ltr'};
  const Q = pid => pid === 'p31' ? 0.5 : 1;
  const left = l => Math.max(0, Math.ceil(Math.round((l.need - l.got) * 2e6) / 1e6) / 2);
  M.reset = (plan, extra) => {
    M.lines = plan.map(([id, need]) => ({id, name: NAMES[id] || id, need, got: 0}));
    M.reg = new Map(); M.seq = 0; M.hold = null; M.noscan = false; M.done = false; M.open = true; M.rev = 0;
    M.log = []; M.viol = []; M.faults = 0; M.fault = null; M.faultLeft = 0; M.xtap = 0;
    (extra || []).forEach(([code, pid, origin, supply]) => M.reg.set(code, {
      pid, origin, district: origin, status: 'active', supply, qty: Q(pid), label: 'F-' + code}));
  };
  M.view = () => {
    const need = M.lines.reduce((a, l) => a + l.need, 0), got = M.lines.reduce((a, l) => a + l.got, 0);
    const h = M.hold && M.hold.until > Date.now() ? M.hold : null;
    return {supply_id: M.sid, district: M.oid, district_code: 'B1', district_name: 'JVC',
      need, got, left: Math.max(0, need - got), positions: M.lines.length, erev: 0, rev: M.rev,
      lines: M.lines.map(l => ({id: l.id, name: l.name, need: l.need, got: l.got, left: left(l)})),
      hold: h ? {live: h.who !== M.me, who: h.who, kind: 'driver'} : {live: false, who: ''},
      noscan_at: M.noscan ? '2026-09-21T10:00:00' : '', done_at: M.done ? '2026-09-21T11:00:00' : '',
      cancelled_at: '', extra: false, prices_ok: true, driver: M.me, claimed_at: 'x', started_at: 'x',
      short: null, mine: true};
  };
  const busy = who => M.hold && M.hold.until > Date.now() && M.hold.who !== who;
  const ours = d => d && d.supply === M.sid && d.origin === M.oid && d.district === M.oid && d.status === 'active';
  M.scan = (b, who) => {
    if(!M.open) return {ok: false, verdict: 'no_supply'};
    if(M.done) return {ok: false, verdict: 'closed'};
    if(busy(who)) return {ok: false, verdict: 'busy', by: M.hold.who, kind: 'driver'};
    const l = M.lines.find(x => x.id === b.product_id);
    if(!l) return {ok: false, verdict: 'not_in_supply'};
    const d = M.reg.get(b.code);
    if(d){
      const o = ours(d);
      return {ok: false, verdict: 'known', name: l.name, label: d.label, district: d.district, at: '',
              need: l.need, got: l.got, ours: o, as_id: o ? d.pid : '', as_name: o ? (NAMES[d.pid] || d.pid) : '', rev: M.rev};
    }
    if(l.got + Q(l.id) > l.need + 1e-9) return {ok: false, verdict: 'full', need: l.need, got: l.got, name: l.name, rev: M.rev};
    l.got = Math.round((l.got + Q(l.id)) * 2) / 2;
    M.rev++;
    M.seq++;
    const label = `${l.id.toUpperCase()}#${String(M.seq).padStart(6, '0')}`;
    M.reg.set(b.code, {pid: l.id, origin: M.oid, district: M.oid, status: 'active', supply: M.sid, qty: Q(l.id), label});
    M.hold = {who, until: Date.now() + 40000};
    let finished = false;
    if(M.noscan && M.lines.every(x => x.got >= x.need)){ M.done = true; finished = true; M.hold = null; }
    return {ok: true, verdict: 'taken', label, code: b.code, name: l.name, need: l.need, got: l.got,
            qty: Q(l.id), unit: 1, left: left(l), flags: [], task_got: 0, finished, supply_done: false, rev: M.rev};
  };
  M.undo = (b, who) => {
    if(!M.open || M.done) return {ok: false, verdict: 'closed'};
    if(busy(who)) return {ok: false, verdict: 'busy', by: M.hold.who, kind: 'driver'};
    const d = M.reg.get(b.code);
    if(!d || d.supply !== M.sid || d.origin !== M.oid) return {ok: false, verdict: 'not_ours'};
    if(!ours(d)) return {ok: false, verdict: 'moved'};
    const l = M.lines.find(x => x.id === d.pid);
    if(!l || l.got < d.qty) return {ok: false, verdict: 'not_ours'};
    l.got = Math.round((l.got - d.qty) * 2) / 2;
    M.reg.delete(b.code);
    M.rev++;
    return {ok: true, code: b.code, product_id: l.id, name: l.name, label: d.label,
            need: l.need, got: l.got, left: left(l), rev: M.rev};
  };
  M.holdReq = (b, who) => {
    if(M.done) return {ok: false, verdict: 'closed'};
    if(busy(who)) return {ok: false, verdict: 'busy', by: M.hold.who};
    if(b.on) M.hold = {who, until: Date.now() + 40000};
    else if(M.hold && M.hold.who === who) M.hold = null;
    return {ok: true};
  };
  // Кто сейчас «в режиме убрать» — спрашиваем само приложение в момент запроса.
  // RCV_RM объявлен через let — свойства window у него нет, берём по имени.
  const rmNow = () => APP === 'driver' ? (typeof RM !== 'undefined' && !!RM) : (typeof RCV_RM !== 'undefined' && !!RCV_RM);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const lat = () => M.lat[0] + Math.random() * (M.lat[1] - M.lat[0]);
  async function serve(kind, body, fn){
    M.pending++;
    try{
      let fault = null;
      if(M.faultLeft > 0 && (kind === 'scan' || kind === 'undo' || kind === 'view')){
        fault = M.fault; M.faultLeft--; M.faults++;
      }
      await sleep(lat());
      if(fault === 'lost_req'){ const e = new Error('network'); e.status = 0; throw e; }
      const res = fn();
      M.log.push({t: Date.now(), kind, body: JSON.parse(JSON.stringify(body || {})), res: JSON.parse(JSON.stringify(res)), rm: rmNow()});
      await sleep(lat());
      if(fault === 'lost_resp'){ const e = new Error('network'); e.status = 0; throw e; }
      return JSON.parse(JSON.stringify(res));
    }finally{ M.pending--; }
  }
  function check(kind, body){
    // Дисциплина режима «убрать»: сканы не уходят, пока он включён; «убрать»
    // уходит только из него или по нажатию крестика.
    if(kind === 'scan' && rmNow()) M.viol.push('скан ушёл в режиме «убрать»: ' + body.code);
    if(kind === 'undo'){
      if(!rmNow() && M.xtap <= 0) M.viol.push('«убрать» ушло без режима и без крестика: ' + body.code);
      if(M.xtap > 0) M.xtap--;
    }
  }
  async function route(path, opts){
    opts = opts || {};
    const m = (opts.method || 'GET').toUpperCase(), b = opts.body || {};
    const who = APP === 'driver' ? M.me : (b.as || M.me);
    if(/\/(task\/)?scan$/.test(path) && m === 'POST'){ check('scan', b); return serve('scan', b, () => M.scan(b, who)); }
    if(/\/(task\/)?undo$/.test(path) && m === 'POST'){ check('undo', b); return serve('undo', b, () => M.undo(b, who)); }
    if(/\/(task\/)?hold$/.test(path) && m === 'POST') return serve('hold', b, () => M.holdReq(b, who));
    if(APP === 'driver' && path === `/api/driver/supply/${M.sid}` && m === 'GET') return serve('view', {}, () => M.view());
    if(APP !== 'driver' && path === `/api/owner/supply/${M.sid}/tasks` && m === 'GET') return serve('view', {}, () => ({tasks: [M.view()]}));
    const e = new Error('stand: нет такой ручки ' + m + ' ' + path); e.status = 404; throw e;
  }
  if(APP === 'driver') window.drvApi = {AMBAR_API: '', drvFetch: route};
  else window.ownerApi = {AMBAR_API: '', ownerFetch: route, getInitData: () => 'x'};
})();
