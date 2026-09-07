/* Клавиатура и поля ввода — одно правило на все три приложения.
 *
 * Беда одна и та же везде: человек нажал в поле, снизу выехала клавиатура, а
 * над ней остались наши же фиксированные полосы — вкладки, итог смены, кнопка
 * действия, — и поле оказалось под ними: пишешь и не видишь, что пишешь.
 *
 * Пока в поле стоит фокус, на <html> висит класс `kb`. Каждое приложение своим
 * CSS прячет по нему нижние полосы (кроме тех, где само поле, — строка чата) и
 * подкладывает воздух снизу, чтобы поле можно было докрутить до середины
 * экрана. Само докручивание — здесь: после фокуса и при каждом изменении
 * visualViewport поле, оказавшееся вне видимой части, подтягивается в центр.
 *
 * Заодно то, что телефон делает не так по умолчанию:
 *   • Enter в однострочном поле = «Готово»: клавиатура закрывается, если поле
 *     само не решило иначе (preventDefault) и не просит «Далее»;
 *   • у поиска выключены автозамена и заглавные, у чисел — клавиша «Готово».
 *
 * Высота клавиатуры в --kb (px) — для тех, кому нужно встать ровно над ней.
 */
(function(){
  const SEL = 'input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range])'
            + ':not([type=button]):not([type=submit]):not([type=color]),textarea,[contenteditable="true"]';
  const root = document.documentElement;
  const vv = window.visualViewport;
  let cur = null, timers = [];

  function kbHeight(){
    if(!vv) return 0;
    return Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
  }
  let apply = function(){
    const h = kbHeight();
    root.style.setProperty('--kb', h + 'px');
    // Класс — по фокусу, а не по высоте: в телеграме на айфоне вебвью сам
    // уменьшается под клавиатуру, и visualViewport про неё не знает.
    root.classList.toggle('kb', !!cur);
    root.classList.toggle('kb-up', h > 40);
  };
  function reveal(){
    if(!cur || !document.contains(cur) || document.activeElement !== cur) return;
    const r = cur.getBoundingClientRect();
    const top = vv ? vv.offsetTop : 0, h = vv ? vv.height : window.innerHeight;
    const lo = top + 64, hi = top + h - 20;
    if(r.top < lo || r.bottom > hi){
      try{ cur.scrollIntoView({block: 'center', behavior: 'smooth'}); }
      catch(e){ try{ cur.scrollIntoView(); }catch(e2){} }
    }
  }
  function schedule(){
    timers.forEach(clearTimeout);
    timers = [120, 380, 760].map(t => setTimeout(() => { apply(); reveal(); }, t));
  }

  // То, что телефон делает не так по умолчанию, — поправить до фокуса:
  // тип клавиатуры и её кнопка выбираются в момент, когда поле его получает.
  function prep(el){
    if(!el || !el.matches || !el.matches(SEL)) return;
    const ph = (el.getAttribute('placeholder') || '').toLowerCase();
    const search = el.type === 'search' || /^(поиск|найти|начните вводить)/.test(ph)
                || /(^|\s)(q|search|find)(\s|$)/i.test(el.id || '');
    if(search){
      if(!el.hasAttribute('autocorrect')) el.setAttribute('autocorrect', 'off');
      if(!el.hasAttribute('autocapitalize')) el.setAttribute('autocapitalize', 'off');
      if(!el.hasAttribute('spellcheck')) el.setAttribute('spellcheck', 'false');
      if(!el.hasAttribute('enterkeyhint')) el.setAttribute('enterkeyhint', 'search');
    }
    const im = el.getAttribute('inputmode') || '';
    if((im === 'numeric' || im === 'decimal' || im === 'tel' || el.type === 'number')
       && !el.hasAttribute('enterkeyhint')) el.setAttribute('enterkeyhint', 'done');
  }
  document.addEventListener('pointerdown', e => prep(e.target), true);
  document.addEventListener('DOMContentLoaded', () => {
    try{ document.querySelectorAll(SEL).forEach(prep); }catch(e){}
  });

  document.addEventListener('focusin', e => {
    const el = e.target;
    if(!el || !el.matches || !el.matches(SEL)) return;
    prep(el);
    cur = el; apply(); schedule();
  });
  document.addEventListener('focusout', () => {
    setTimeout(() => {
      const a = document.activeElement;
      if(!a || a === document.body || !a.matches || !a.matches(SEL)){
        cur = null; timers.forEach(clearTimeout); apply();
      }
    }, 80);
  });
  if(vv){
    vv.addEventListener('resize', () => { apply(); if(cur) schedule(); });
    vv.addEventListener('scroll', apply);
  }

  // Enter = «Готово» в однострочном поле: закрыть клавиатуру. Поле, у которого
  // Enter значит своё (отправить, дальше), останавливает событие само.
  document.addEventListener('keydown', e => {
    if(e.key !== 'Enter' || e.defaultPrevented) return;
    const el = e.target;
    if(!el || !el.matches || !el.matches('input') || el.form) return;
    const hint = el.getAttribute('enterkeyhint');
    if(hint === 'next' || hint === 'send' || hint === 'go') return;
    setTimeout(() => { try{ if(document.activeElement === el) el.blur(); }catch(x){} }, 0);
  });

  // Цифровая клавиатура айфона без «Готово»: закрыть её можно только тапом
  // мимо поля, и человек ищет, куда бы ткнуть. Над клавиатурой — своя
  // маленькая «Готово» для числовых полей. Панель пересчёта у владельца
  // (.stk-bar) сама умеет то же — там кнопку не показываем.
  let done = null;
  function doneBtn(){
    if(done) return done;
    done = document.createElement('button');
    done.type = 'button'; done.textContent = 'Готово'; done.setAttribute('aria-label', 'Готово');
    Object.assign(done.style, {
      position: 'fixed', right: '12px', bottom: 'calc(var(--kb, 0px) + 10px)', zIndex: '9999',
      display: 'none', height: '36px', padding: '0 16px', borderRadius: '999px', border: '0',
      fontFamily: 'inherit', fontSize: '13px', fontWeight: '700', letterSpacing: '.2px',
      color: '#1a1408', background: 'linear-gradient(135deg,#C9A96E,#E8C98A)',
      boxShadow: '0 8px 24px rgba(0,0,0,.45)', cursor: 'pointer',
    });
    // pointerdown, не click: к клику фокус уже ушёл бы с поля и кнопка
    // пряталась бы раньше, чем сработает.
    done.addEventListener('pointerdown', e => {
      e.preventDefault();
      try{ if(cur) cur.blur(); }catch(x){}
      try{ if(document.activeElement && document.activeElement.blur) document.activeElement.blur(); }catch(x){}
    });
    document.body.appendChild(done);
    return done;
  }
  function doneShow(){
    const im = cur ? (cur.getAttribute('inputmode') || '') : '';
    const numeric = cur && (im === 'numeric' || im === 'decimal' || im === 'tel' || cur.type === 'number');
    const own = cur && cur.classList && cur.classList.contains('stk-qty');
    doneBtn().style.display = numeric && !own ? 'block' : 'none';
  }
  const _apply = apply;
  apply = function(){ _apply(); doneShow(); };

  window.AMBAR_KB = {height: kbHeight, reveal, current: () => cur};
})();
