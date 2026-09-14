/*
 * AMBAR — экран «Не удалось загрузить», общий для всех мини-аппов.
 *
 * Голая красная строка посреди пустого экрана читалась как «приложение
 * сломалось». Владелец (14 сен 2026) попросил показывать картинку: лист с
 * грустным лицом, облака и красный восклицательный знак, под ним — что
 * случилось и что делать. Рисунок — встроенный SVG (никаких картинок по сети:
 * если не грузится, то и она бы не загрузилась).
 *
 *   loadFail()                  → разметка без кнопки
 *   loadFail("chkLoad()")       → с кнопкой «Попробовать ещё раз» (строка onclick)
 *
 * Стили подключает сам, один раз.
 */
(function(){
  // justify-content:center — когда контейнеру задали высоту (приложение
  // водителя добивает блок до низа экрана через fitFill), картинка встаёт по
  // центру пустого места, как на макете; без высоты — просто сверху.
  var CSS = '.lf{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:34px 24px 26px;box-sizing:border-box}'
    + '.lf-pic{width:214px;max-width:70%;height:auto;display:block}'
    + '.lf-t{margin-top:14px;font-size:21px;font-weight:700;line-height:1.2;letter-spacing:-.3px;color:#F1F2F6}'
    + '.lf-s{margin-top:8px;font-size:14px;line-height:1.5;color:#8B8FA8;max-width:270px}'
    + '.lf-b{margin-top:18px;height:44px;padding:0 22px;border-radius:12px;border:1px solid rgba(201,169,110,.45);'
    + 'background:rgba(201,169,110,.10);color:#E8C98A;font:600 14.5px/1 inherit;font-family:inherit;cursor:pointer}'
    + '.lf-b:active{background:rgba(201,169,110,.18)}';
  function css(){
    if(document.getElementById('lfCss')) return;
    var s = document.createElement('style'); s.id = 'lfCss'; s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }
  // Лист чуть наклонён, угол загнут, на нём грустное лицо и две строки
  // чек-листа; справа снизу — знак «!» в красном круге со свечением; сзади —
  // облака и тень. Цвета — палитра приложений: графит, лиловый серый, красный.
  var PIC = '<svg class="lf-pic" viewBox="0 0 300 250" fill="none" aria-hidden="true">'
    + '<defs>'
    + '<linearGradient id="lfDoc" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2E3040"/><stop offset="1" stop-color="#1B1D26"/></linearGradient>'
    + '<linearGradient id="lfFold" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3B3E4E"/><stop offset="1" stop-color="#24262F"/></linearGradient>'
    + '<radialGradient id="lfShadow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#000" stop-opacity=".55"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>'
    + '<radialGradient id="lfGlow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#E8746F" stop-opacity=".45"/><stop offset="1" stop-color="#E8746F" stop-opacity="0"/></radialGradient>'
    + '<radialGradient id="lfBadge" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#4A2429"/><stop offset="1" stop-color="#2A171B"/></radialGradient>'
    + '</defs>'
    // облака
    + '<g fill="#171923" stroke="#262935" stroke-width="1.5">'
    + '<path d="M18 128c-8 0-13-6-13-12s5-12 13-12c2-9 10-15 20-15s18 6 20 15h2c8 0 13 6 13 12s-5 12-13 12z"/>'
    + '<path d="M238 176c-6 0-10-5-10-10s4-10 10-10c2-7 8-12 16-12s14 5 16 12h1c6 0 10 5 10 10s-4 10-10 10z"/>'
    + '</g>'
    // тень
    + '<ellipse cx="150" cy="222" rx="96" ry="14" fill="url(#lfShadow)"/>'
    // лист
    + '<g transform="rotate(-5 150 120)">'
    + '<path d="M96 30h72l38 38v112a14 14 0 0 1-14 14H96a14 14 0 0 1-14-14V44a14 14 0 0 1 14-14z" fill="url(#lfDoc)" stroke="#3E4150" stroke-width="1.5"/>'
    + '<path d="M168 30v24a14 14 0 0 0 14 14h24z" fill="url(#lfFold)" stroke="#3E4150" stroke-width="1.5" stroke-linejoin="round"/>'
    // лицо
    + '<circle cx="130" cy="84" r="4.2" fill="#C9605E"/><circle cx="162" cy="84" r="4.2" fill="#C9605E"/>'
    + '<path d="M128 112c6-9 30-9 36 0" stroke="#C9605E" stroke-width="5" stroke-linecap="round"/>'
    // строки чек-листа
    + '<rect x="108" y="132" width="15" height="15" rx="3.5" stroke="#4E5162" stroke-width="2.4"/><rect x="132" y="137" width="42" height="5" rx="2.5" fill="#4E5162"/>'
    + '<rect x="108" y="156" width="15" height="15" rx="3.5" stroke="#4E5162" stroke-width="2.4"/><rect x="132" y="161" width="42" height="5" rx="2.5" fill="#4E5162"/>'
    + '</g>'
    // искры
    + '<g stroke="#4E5162" stroke-width="2.4" stroke-linecap="round"><path d="M226 66l9-7M232 82l11-2"/><path d="M62 156l-9 6M56 140l-11 1"/></g>'
    // знак
    + '<circle cx="208" cy="176" r="50" fill="url(#lfGlow)"/>'
    + '<circle cx="208" cy="176" r="28" fill="url(#lfBadge)" stroke="#C9605E" stroke-width="2.5"/>'
    + '<rect x="204" y="158" width="8" height="22" rx="4" fill="#E8746F"/><circle cx="208" cy="189" r="4.5" fill="#E8746F"/>'
    + '</svg>';
  window.loadFail = function(retry, title, sub){
    css();
    return '<div class="lf">' + PIC
      + '<div class="lf-t">' + (title || 'Не удалось загрузить') + '</div>'
      + '<div class="lf-s">' + (sub || 'Проверьте подключение к интернету и попробуйте ещё раз.') + '</div>'
      + (retry ? '<button class="lf-b" type="button" onclick="' + retry + '">Попробовать ещё раз</button>' : '')
      + '</div>';
  };
})();
