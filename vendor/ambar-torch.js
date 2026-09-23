/* Свет камеры: фонарик со ступенями. Один на все приложения.
 *
 * Владелец, 22 сен 2026: «можем сделать так, чтобы можно было регулировать
 * яркость вот этого фонарика? но с точки зрения дизайна и механического
 * решения надо сделать всё максимально органично и продуманно»; «и у
 * старшего тоже, вообще везде, где используется фонарик».
 *
 * Чего в вебе нет. У самой вспышки яркости не существует: телефон отдаёт её
 * одним выключателем — torch: true/false, — и ступеней там нет ни на андроиде,
 * ни на айфоне (у настоящего приложения на айфоне есть torchLevel, но мини-аппу
 * его не дают). Мигать вспышкой, чтобы получилось «полсилы», нельзя: каждое
 * переключение идёт десятки миллисекунд, кадр дрожит, а сканер на дрожащем
 * кадре не читает. Притворяться нечем — и не притворяемся.
 *
 * Что регулируется на самом деле. Фонарик включают не ради светодиода, а ради
 * того, чтобы видеть: код на крышке, лицо в темноте. Вот это — сколько света в
 * кадре — камера отдаёт настоящим диапазоном (exposureCompensation, у кого-то
 * brightness). Убавили — блик с крышки ушёл, код прочитался; прибавили — снова
 * светло. На экране это выглядит ровно как яркость фонарика, только честно.
 *
 * Жест взят наш же, из звонка: тянешь палец по кнопке вверх-вниз, под ней
 * разворачивается линейка из восьми штрихов. Ничего нового учить не надо, а
 * простое нажатие как было включением, так им и осталось.
 *
 * Телефон диапазона не дал — линейки нет вовсе: кнопка работает как раньше.
 * Лучше без регулировки, чем ползунок, который ничего не двигает.
 */
(function (global) {
  'use strict';

  var STEPS = 8;                 // столько же, сколько у экранного света в звонке
  var LS = 'ambar_torch_lvl';
  // Чем убавляем свет в кадре. Только то, что работает при автоматической
  // экспозиции: iso и exposureTime требуют ручного режима, а в ручном камера
  // перестаёт подстраиваться сама и кадр уплывает на первой же тени.
  var KEYS = ['exposureCompensation', 'brightness'];

  var level = STEPS;             // 8 — как было до всякой регулировки
  try {
    var saved = parseInt(localStorage.getItem(LS), 10);
    if (saved >= 1 && saved <= STEPS) level = saved;
  } catch (e) {}

  function capsOf(t) {
    try { return (t && t.getCapabilities ? t.getCapabilities() : null) || {}; }
    catch (e) { return {}; }
  }

  function setsOf(t) {
    try { return (t && t.getSettings ? t.getSettings() : null) || {}; }
    catch (e) { return {}; }
  }

  // Чем именно убавлять свет у этой камеры — или ничем. Считаем один раз на
  // дорожку и запоминаем: верх шкалы — то, как камера снимала ДО нас, а после
  // первого же применения она отдаёт в настройках уже нашу ступень, и точка
  // отсчёта уехала бы вслед за пальцем. Пустой ответ не запоминаем: вспышку и
  // диапазоны телефон иногда отдаёт не сразу, а после первых кадров.
  var memo = new WeakMap();

  function dimOf(t) {
    if (!t) return null;
    if (memo.has(t)) return memo.get(t);
    var d = dimRead(t);
    if (d) memo.set(t, d);
    return d;
  }

  function dimRead(t) {
    var c = capsOf(t), s = setsOf(t);
    for (var i = 0; i < KEYS.length; i++) {
      var k = KEYS[i], r = c[k];
      if (!r || typeof r.min !== 'number' || typeof r.max !== 'number') continue;
      if (!(r.max > r.min)) continue;
      // Верх шкалы — то, как камера снимает сама: полный свет это её обычный
      // кадр, а не выкрученный до упора. Если камера уже стоит на дне, вниз
      // идти некуда — берём верх диапазона.
      var def = typeof s[k] === 'number' ? s[k] : r.max;
      if (!(def > r.min)) def = r.max;
      return {key: k, min: r.min, def: def, step: r.step || 0};
    }
    return null;
  }

  function valueFor(d, lvl) {
    var v = d.min + (d.def - d.min) * (Math.max(1, Math.min(STEPS, lvl)) - 1) / (STEPS - 1);
    if (d.step > 0) v = d.min + Math.round((v - d.min) / d.step) * d.step;
    return Math.min(d.def, Math.max(d.min, v));
  }

  // Пока одно применение в пути, следующее ждёт: палец успевает проехать всю
  // линейку за полсекунды, а applyConstraints идёт до сотни миллисекунд, и
  // очередь из восьми запросов кончается тем, что свет встаёт не там, где
  // палец. Держим только последнее желание.
  var busy = false, want = null;

  function pump() {
    if (busy || !want) return;
    var job = want; want = null; busy = true;
    var done = function () { busy = false; pump(); };
    try { job().then(done, done); } catch (e) { done(); }
  }

  function push(fn) { want = fn; pump(); }

  function dimApply(t, lvl) {
    var d = dimOf(t);
    if (!d || !t || !t.applyConstraints) return Promise.resolve(false);
    var c = {};
    c[d.key] = valueFor(d, lvl);
    // Без advanced: это пожелание, а не требование — камера, которая такого
    // не умеет, просто оставит как есть и не уронит нам всю дорожку.
    return t.applyConstraints({advanced: [c]}).then(function () { return true; },
                                                    function () { return false; });
  }

  function dimReset(t) {
    var d = dimOf(t);
    if (!d || !t || !t.applyConstraints) return Promise.resolve(false);
    var c = {};
    c[d.key] = d.def;
    return t.applyConstraints({advanced: [c]}).then(function () { return true; },
                                                    function () { return false; });
  }

  // ── запасной путь: гасим не свет, а картинку ─────────────────────────────
  // Владелец, 23 сен 2026: «у меня не работает, сделай так, чтобы вообще на
  // любом телефоне работало». На его телефоне камера не отдаёт ни одного
  // диапазона света (в журнале: «вспышка есть · ступеней нет»), и управлять
  // там нечем — это предел браузера, а не наша лень.
  //
  // Что можно честно: приглушить то, что человек видит на экране. Сам
  // светодиод при этом светит как светил — и мы об этом прямо говорим, а не
  // делаем вид, что крутим вспышку. Фильтр висит ТОЛЬКО на картинке: сканер
  // читает исходный кадр, поэтому читаться хуже не станет.
  var screenEl = null, screenSaid = false;

  function screenSet(el) {
    if (screenEl && screenEl !== el) screenEl.style.filter = '';
    screenEl = el || null;
  }

  // Чем эта камера умеет убавлять свет: 'cam' — по-настоящему, экспозицией;
  // 'screen' — только картинкой; null — нечем вовсе.
  function modeOf(t) {
    if (t && dimOf(t)) return 'cam';
    return screenEl ? 'screen' : null;
  }

  function screenPaint(on) {
    if (!screenEl) return;
    if (!on) { screenEl.style.filter = ''; return; }
    // Нижняя ступень — 45%: темнее уже не разглядеть, куда наводить.
    var k = 0.45 + 0.55 * (Math.max(1, Math.min(STEPS, level)) - 1) / (STEPS - 1);
    screenEl.style.filter = k >= 0.999 ? '' : 'brightness(' + k.toFixed(2) + ')';
  }

  // ── линейка ───────────────────────────────────────────────────────────────
  // Одна на всё приложение и висит поверх всего: кнопки фонарика живут и в
  // полосе сканера, и в углу кадра, и у каждой свои обрезки по краям. Своя
  // позиция от кнопки избавляет от разговора с чужой вёрсткой.
  var dial = null;

  function dialEl() {
    if (dial) return dial;
    var st = document.createElement('style');
    st.textContent =
      '.atl-dial{position:fixed;z-index:9000;display:flex;flex-direction:column;' +
      'align-items:flex-start;gap:9px;pointer-events:none;opacity:0;' +
      'transition:opacity .18s ease}' +
      '.atl-dial.on{opacity:1}' +
      '.atl-dial i{display:block;height:2px;width:11px;border-radius:2px;' +
      'background:rgba(255,255,255,.45);' +
      'transition:width .16s cubic-bezier(.32,.72,0,1),background .16s ease}' +
      '.atl-dial i.near{width:17px;background:rgba(255,255,255,.7)}' +
      '.atl-dial i.now{width:27px;background:#fff;box-shadow:0 0 10px rgba(255,244,226,.85)}';
    document.head.appendChild(st);
    dial = document.createElement('div');
    dial.className = 'atl-dial';
    dial.setAttribute('aria-hidden', 'true');
    for (var i = 0; i < STEPS; i++) dial.appendChild(document.createElement('i'));
    document.body.appendChild(dial);
    return dial;
  }

  function dialPaint() {
    if (!dial) return;
    for (var i = 0; i < dial.children.length; i++) {
      var далеко = Math.abs(i + 1 - level);
      dial.children[i].className = далеко === 0 ? 'now' : далеко === 1 ? 'near' : '';
    }
  }

  // Штрихи выезжают из-под кнопки друг за другом: линейка не возникает
  // целиком, а разворачивается — видно, откуда она взялась (как в звонке).
  function dialShow(btn, show) {
    var d = dialEl();
    if (!show) { d.classList.remove('on'); return; }
    var r = btn.getBoundingClientRect();
    d.style.left = Math.round(r.left + r.width / 2 - 13) + 'px';
    d.style.top = Math.round(r.bottom + 10) + 'px';
    dialPaint();
    d.classList.add('on');
    for (var i = 0; i < d.children.length; i++) {
      try {
        d.children[i].animate([{transform: 'translateX(-14px)', opacity: 0}, {transform: 'none', opacity: 1}],
                              {duration: 240, delay: i * 22, fill: 'backwards',
                               easing: 'cubic-bezier(.32,.72,0,1)'});
      } catch (e) {}
    }
  }

  // ── жест ──────────────────────────────────────────────────────────────────
  // Нажал — включил или выключил, как раньше. Повёл пальцем, не отпуская, —
  // свет зажигается (если был погашен) и идёт по ступеням за пальцем.
  function attach(btn, o) {
    if (!btn || btn._atl) return;
    btn._atl = true;
    // У кнопок сканера onclick живёт прямо в разметке. Оставить его нельзя:
    // после пальца браузер шлёт click сам, и фонарик включился бы и тут же
    // погас. Снимаем и зовём то же самое сами — а если этот файл не загрузился,
    // атрибут остаётся на месте и кнопка работает по-старому.
    btn.removeAttribute('onclick');
    var y0 = null, было = level, поехали = false, тык = 0;

    var можно = function () {
      var t = o.track && o.track();
      return !!modeOf(t);
    };

    var начало = function (y) {
      тык = Date.now();
      y0 = y; было = level; поехали = false;
      if (o.on && o.on() && можно()) dialShow(btn, true);
    };

    var ход = function (y) {
      if (y0 === null) return;
      if (!можно()) {
        // Тянут, а ступеней нет — надо сказать. Молчание в ответ на жест
        // читается как «не сделали», хотя это телефон не отдаёт яркость
        // (владелец, 23 сен 2026: «в свободной проверке тоже реализуй»).
        if (o.nope && Math.abs(y0 - y) > 12 && !btn._atlSaid) {
          btn._atlSaid = true;
          o.nope();
        }
        return;
      }
      var d = y0 - y;
      if (!поехали) {
        if (Math.abs(d) < 8) return;                 // это ещё нажатие, а не движение
        поехали = true;
        if (o.light && (!o.on || !o.on())) o.light();
        dialShow(btn, true);
      }
      setLevel(было + Math.round(d / 22), o);
    };

    var конец = function () {
      if (y0 === null) return;
      var тянули = поехали;
      y0 = null; поехали = false;
      dialShow(btn, false);
      if (!тянули && o.toggle) o.toggle();
    };

    // На телефоне ведём по настоящим касаниям и сами отменяем прокрутку: одного
    // touch-action мало — телеграм решает судьбу вертикального движения раньше,
    // чем оно дойдёт до указателей (в звонке напоролись на это же).
    if ('ontouchstart' in window) {
      btn.addEventListener('touchstart', function (e) { начало(e.touches[0].clientY); }, {passive: true});
      btn.addEventListener('touchmove', function (e) {
        if (y0 !== null && поехали) e.preventDefault();
        ход(e.touches[0].clientY);
      }, {passive: false});
      btn.addEventListener('touchend', конец);
      btn.addEventListener('touchcancel', конец);
    } else {
      btn.addEventListener('pointerdown', function (e) {
        начало(e.clientY);
        try { btn.setPointerCapture(e.pointerId); } catch (x) {}
      });
      btn.addEventListener('pointermove', function (e) { ход(e.clientY); });
      btn.addEventListener('pointerup', конец);
      btn.addEventListener('pointercancel', конец);
    }
    // Колесо — чтобы проверять на настольном браузере; на телефоне его нет.
    btn.addEventListener('wheel', function (e) {
      if (!(o.on && o.on()) || !можно()) return;
      setLevel(level + (e.deltaY < 0 ? 1 : -1), o);
    }, {passive: true});
    // Если указателей в браузере нет вовсе, кнопка обязана работать по-старому.
    // Клавиатура и мышь без указателей: жеста не было — значит обычное нажатие.
    btn.addEventListener('click', function () { if (Date.now() - тык > 700 && o.toggle) o.toggle(); });
  }

  function setLevel(n, o) {
    var было = level;
    level = Math.max(1, Math.min(STEPS, n));
    if (level === было) return;
    try { localStorage.setItem(LS, String(level)); } catch (e) {}
    if (o && o.hap) o.hap('sel');                    // щелчок на каждой ступени
    dialPaint();
    var t = o && o.track && o.track();
    if (t && dimOf(t)) push(function () { return dimApply(t, level); });
    else {
      screenPaint(true);
      // Сказать один раз, что именно убавляется: человек тянет за «яркость
      // фонарика», а гаснет картинка — молчать об этом нечестно.
      if (o && o.dimOnly && !screenSaid) { screenSaid = true; o.dimOnly(); }
    }
  }

  global.AmbarTorch = {
    STEPS: STEPS,
    get level() { return level; },
    // Поставить ступень снаружи (линейка звонка): o — {track, hap}.
    set: function (n, o) { setLevel(n, o); },
    // Есть ли вспышка у этой дорожки.
    has: function (t) { return !!capsOf(t).torch; },
    // Можно ли на этой камере убавлять свет (а значит — показывать линейку).
    dimmable: function (t) { return !!dimOf(t); },
    // Включить или выключить с текущей ступенью. Свет — первым делом: человек
    // нажал ради света, а не ради экспозиции; гасим — возвращаем кадр как был,
    // иначе в темноте останется приглушённая картинка.
    apply: function (t, on) {
      if (!t || !t.applyConstraints) return Promise.resolve(false);
      return t.applyConstraints({advanced: [{torch: !!on}]}).then(function () {
        if (dimOf(t)) push(function () { return on ? dimApply(t, level) : dimReset(t); });
        else screenPaint(!!on);      // погас фонарик — вернули картинку как была
        return true;
      }, function () { return false; });
    },
    attach: attach,
    // Куда смотрит человек: этот <video> и приглушаем, когда камера своего
    // диапазона не даёт. Зовётся при запуске сканера и при его остановке.
    screen: screenSet,
    mode: modeOf,
    hide: function () { if (dial) dial.classList.remove('on'); },
    // Зажгли фонарик — линейка на секунду показывается сама. Так человек
    // узнаёт, что ступени есть, не читая никаких подсказок: спрятанный жест,
    // о котором никто не догадался, — это отсутствующий жест.
    peek: function (btn, t) {
      if (!btn || !dimOf(t)) return;
      dialShow(btn, true);
      clearTimeout(btn._atlT);
      btn._atlT = setTimeout(function () { dialShow(btn, false); }, 900);
    },
    // Что эта камера вообще умеет — строкой в журнал (только для отладки:
    // почему у одного телефона ступени есть, а у другого нет). Пишем ВЕСЬ
    // список того, что она отдаёт: телефон может не давать экспозицию, но
    // давать что-то другое, и не зная об этом, спорить не о чем.
    diag: function (t) {
      var c = capsOf(t), d = dimOf(t), out = ['вспышка ' + (c.torch ? 'есть' : 'нет')];
      out.push(d ? ('ступени по ' + d.key) : 'ступеней нет');
      for (var i = 0; i < KEYS.length; i++) {
        var r = c[KEYS[i]];
        if (r) out.push(KEYS[i] + ' ' + r.min + '..' + r.max);
      }
      var есть = [];
      for (var k in c) { if (Object.prototype.hasOwnProperty.call(c, k)) есть.push(k); }
      if (есть.length) out.push('умеет: ' + есть.sort().join(','));
      return out.join(' · ');
    },
  };
})(window);
