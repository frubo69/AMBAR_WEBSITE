/*
 * ambar-call.js — голосовой звонок между приложением водителя и панелью
 * оператора. Один файл на оба приложения: протокол у них общий, и разъехаться
 * двум копиям здесь нельзя — разъедутся, и звонок молча перестанет собираться.
 *
 * Что делает: держит сокет сигналинга, микрофон, соединение, тоны и качество
 * связи, а наружу отдаёт голые события. Никакой разметки — как выглядит
 * звонок, каждое приложение решает само.
 *
 * Микрофон — один раз за сеанс
 * ----------------------------
 * Раньше дорожка останавливалась после каждого разговора, и следующий звонок
 * снова упирался в системное окно. Теперь микрофон берётся однажды и живёт
 * до конца сеанса; между звонками дорожка ВЫКЛЮЧАЕТСЯ (enabled = false), а не
 * останавливается — окна больше нет, и записи тоже нет.
 *
 * Отпускаем его только когда приложение свернули дольше чем на полминуты:
 * иначе телефон всю смену показывал бы, что микрофон занят, и это правильно
 * пугало бы человека.
 *
 * Просить приходится из касания — «позвонить», «принять» или отдельная кнопка
 * на экране связи. В вебвью айфона запрос вне жеста отклоняется молча.
 * Программного способа спросить «от имени бота» не существует: в API мини-аппов
 * есть геопозиция, биометрия, хранилище — микрофона нет, окно рисует система и
 * называет в нём адрес приложения. Поэтому важно, чтобы окно всплывало один раз
 * и в понятный момент, а не посреди входящего звонка.
 *
 * Видео
 * -----
 * Та же дорожка в том же соединении — отдельного канала не нужно. Камера
 * решается в момент вызова: голосовой звонок остаётся голосовым, видео просят
 * явно. Внутри разговора камеру можно выключить и переключить на заднюю, и
 * то и другое без пересборки соединения — меняется только дорожка.
 *
 * Битрейт видео ограничен намеренно: водитель на 4G, и картинка, которая ест
 * весь канал, забирает его у голоса. Голос важнее картинки всегда.
 *
 * Порядок предложения важен: оффер всегда делает звонящий и только после того,
 * как трубку сняли. Если делать раньше, вторая сторона получит его до того, как
 * разрешит микрофон, и ответит соединением без звука.
 */
(function () {
  'use strict';


  // Свернули дольше — отпускаем микрофон. Полминуты оказалось мало: AMBAR STAR
  // сворачивают и разворачивают десятки раз за вечер, и каждый раз система
  // спрашивала разрешение заново. Три минуты закрывают обычное переключение
  // между приложениями; за это время индикатор записи горит, но дорожка
  // выключена и в неё ничего не пишется.
  var MIC_FREE_AFTER = 180000;
  var STATS_EVERY = 3000;        // как часто смотрим на качество связи
  var ICE_RESTART_AFTER = 4000;  // столько ждём, прежде чем пересобирать связь
  var VIDEO_MAX_KBPS = 600;      // потолок картинки: голос важнее её всегда

  // ── тоны ─────────────────────────────────────────────────────────────────
  // Гудки звонящему и звонок принимающему. Человек должен слышать, что связь
  // идёт: молчащий экран «Звоним…» неотличим от сломанного.
  function Tones() {
    this.ac = null;
    this.timer = null;
    this.kind = '';
    this._until = 0;        // до какого времени звучит текущий гудок
    this._pending = false;  // ждём пробуждения контекста
  }
  Tones.prototype._ctx = function () {
    try {
      if (!this.ac) this.ac = new (window.AudioContext || window.webkitAudioContext)();
      if (this.ac.state === 'suspended') this.ac.resume();
    } catch (e) { this.ac = null; }
    return this.ac;
  };
  // Контекст создаётся спящим, а будится не мгновенно. Раньше гудок, попавший
  // в этот промежуток, просто выбрасывался — и первый гудок вызова человек не
  // слышал никогда: тишина, а первый звук приходил только со второго круга,
  // через несколько секунд. Отсюда и ощущение, что гудки запаздывают.
  Tones.prototype._beep = function (freq, dur, gain) {
    var ac = this._ctx(), self = this;
    if (!ac) return;
    // Пока предыдущий гудок звучит, второй не начинаем. Без этого гудки
    // накладывались друг на друга: контекст просыпается не сразу, а отложенные
    // попытки копились и срабатывали все разом.
    var now = Date.now();
    if (now < this._until) return;
    this._until = now + dur * 1000;
    if (ac.state === 'running') { this._play(ac, freq, dur, gain); return; }
    if (this._pending) return;                  // одна отложенная попытка, не больше
    this._pending = true;
    try {
      ac.resume().then(function () {
        self._pending = false;
        self._play(ac, freq, dur, gain);
      }).catch(function () { self._pending = false; });
    } catch (e) { this._pending = false; }
  };
  Tones.prototype._play = function (ac, freq, dur, gain) {
    var o = ac.createOscillator(), g = ac.createGain(), t0 = ac.currentTime;
    var v = gain == null ? 0.16 : gain;
    o.type = 'sine';
    o.frequency.value = freq;
    // Ровный тон с короткими краями. Плавное затухание во всю длину звучало
    // умирающим гудком: в телефоне гудок ровный и обрывается сразу.
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(v, t0 + 0.018);
    g.gain.setValueAtTime(v, t0 + Math.max(0.05, dur - 0.035));
    g.gain.linearRampToValueAtTime(0, t0 + dur);
    o.connect(g); g.connect(ac.destination);
    o.start(t0); o.stop(t0 + dur + 0.02);
  };

  // Разбудить заранее, внутри касания. Контекст, созданный не в жесте, айфон
  // держит спящим — и первый же гудок оказывается в пустоту.
  Tones.prototype.prime = function () { this._ctx(); };

  // Вызов: секунда тона и четыре паузы, 425 Гц — то же, что слышно в трубке
  // обычного телефона. Рисунок узнаваемый, и по нему слышно, что вызов идёт.
  Tones.prototype.ringback = function () { this._loop('ringback', 5000, function (t) {
    t._beep(425, 1.0, 0.10); }); };
  // Входящий: двойная трель аппарата — её ни с гудком, ни с сиреной заказа не
  // спутать.
  Tones.prototype.ring = function () { this._loop('ring', 3000, function (t) {
    t._beep(620, 0.34, 0.2); setTimeout(function () { t._beep(620, 0.34, 0.2); }, 540); }); };
  // Занято: короткий тон вдвое чаще вызова, три круга — дальше человек понял.
  Tones.prototype.busy = function () {
    this.stop();
    var t = this, n = 0;
    (function круг() {
      t._beep(425, 0.35, 0.13);
      if (++n < 3) setTimeout(круг, 700);
    })();
  };
  // Не прошло: не взяли, недоступен, нельзя. Два тона вниз — в телефоне это
  // звучит именно так и означает «соединения не будет».
  Tones.prototype.fail = function () {
    this.stop();
    var t = this;
    t._beep(480, 0.2, 0.12);
    setTimeout(function () { t._beep(360, 0.34, 0.12); }, 240);
  };
  // Конец разговора — один мягкий тон, и всё.
  Tones.prototype.bye = function () { this.stop(); this._beep(330, 0.26, 0.11); };
  Tones.prototype._loop = function (kind, every, fn) {
    if (this.kind === kind) return;
    this.stop();
    this.kind = kind;
    var t = this;
    fn(t);
    this.timer = setInterval(function () { fn(t); }, every);
  };
  Tones.prototype.stop = function () {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.kind = '';
    this._until = 0;
    this._pending = false;
  };
  // Во время разговора аудиоконтекст надо не просто замолчать, а закрыть.
  // Пока он жив, телефон держит звуковую сессию под веб-аудио — со своей
  // частотой дискретизации и своим размером буфера, — а поверх неё играет
  // WebRTC. На айфоне это слышно хрипом и потрескиванием. Тоны нужны только
  // до ответа, поэтому контекст создаётся заново, когда снова понадобится.
  Tones.prototype.release = function () {
    this.stop();
    var ac = this.ac;
    this.ac = null;
    if (ac) { try { ac.close(); } catch (e) {} }
  };

  // ── клиент ───────────────────────────────────────────────────────────────



  function AmbarCall(opts) {
    this.api = (opts.api || location.origin).replace(/\/$/, '');
    this.initData = opts.initData || '';
    this.as = opts.as || '';
    this.on = opts.on || function () {};

    this.ws = null;
    this.pc = null;
    this.stream = null;      // микрофон, живёт между звонками
    this.cam = null;         // камера — только на время видеозвонка
    this.remote = null;      // что пришло от собеседника
    this.facing = 'user';    // какая камера включена
    this.video = false;      // это видеозвонок
    this.audio = null;       // куда играет собеседник
    this.call = null;        // {id, peer, dir, order, note, say}
    this.roster = [];
    this.recent = [];        // последние звонки — приходят с сервера
    this.ice = [{urls: 'stun:stun.l.google.com:19302'}];
    this.me = '';
    this.kind = '';
    this.defaultTarget = '';
    this.quality = '';       // '', 'good', 'weak', 'bad'
    this.route = '';         // 'ear' | 'speaker' — куда идёт голос
    this.tones = new Tones();

    this._retry = 0;
    this._closing = false;
    this._pendingIce = [];
    this._statsT = null;
    this._lastStats = null;
    this._iceRestartT = null;
    this._hideT = null;
    this._wake = null;

    var self = this;
    // Свернули приложение — отпускаем микрофон, но не сразу: короткое
    // переключение на карты не должно стоить нового окна с разрешением.
    document.addEventListener('visibilitychange', function () {
      clearTimeout(self._hideT);
      if (document.hidden) {
        self._hideT = setTimeout(function () {
          if (!self.call) self._freeMic();
        }, MIC_FREE_AFTER);
      } else if (self.call) {
        // Замок берём заново только если экран вообще нужен.
        if (self.video || self.route === 'speaker') self._keepAwake(true);
        // Замок экрана система снимает сама, как только страницу спрятали, и
        // обратно не ставит. Вернулись в приложение посреди разговора — берём
        // заново: иначе экран гаснет прямо во время звонка, а вместе с ним на
        // телефоне засыпает и сам разговор.
        // Съёмку после возвращения система иногда будит сама, а иногда
        // закрывает дорожку насовсем — тогда собеседник больше не увидит
        // ничего, сколько ни жди. Закрыли — берём камеру заново.
        var vt = self.cam && self.cam.getVideoTracks()[0];
        if (self.cam && (!vt || vt.readyState !== 'live')) {
          self._camOn().catch(function () {});
        }
      }
    });
  }

  // Несколько строк с устройства в лог сервера. Не логи ради логов: звук
  // ведёт себя по-разному на каждом телефоне, а увидеть его отсюда нельзя —
  // и трижды подряд это кончалось починкой вслепую.
  AmbarCall.prototype._diag = function (text) {
    if ((this._diagN = (this._diagN || 0) + 1) > 8) return;
    this._send({t: 'diag', text: String(text).slice(0, 200)});
  };

  AmbarCall.prototype._emit = function (t, d) {
    try { this.on(t, d || {}); } catch (e) { console.warn('[call]', e); }
  };

  // Разрешение уже давали — приложение может не показывать объяснение снова.
  AmbarCall.prototype.micGranted = function () {
    try { return localStorage.getItem('ambar_mic_ok') === '1'; } catch (e) { return false; }
  };
  // ── куда идёт голос: в трубку у уха или в громкую связь ─────────────────
  //
  // Из страницы этим управляет ровно одна вещь — тип звуковой сессии. Где его
  // нет, переключать нечего: браузер решает сам и решает в пользу громкой
  // связи. Поэтому кнопку «динамик» показываем только там, где она правда
  // работает: мёртвая кнопка хуже отсутствующей.
  //
  // «Трубка» — сессия разговора: система на айфоне ведёт такую в динамик у уха
  // и сама гасит экран, когда телефон подносят к лицу. «Громкая» — обычное
  // поведение по умолчанию, то самое, что было до этой правки.
  // Выбор канала выключен, и это осознанно.
  //
  // На айфоне трубка у уха недостижима: ни выбором устройства вывода, ни типом
  // звуковой сессии — оба отвечают «готово» и не переключают ничего. Причина
  // глубже кода звонка: любой прямой выход веб-аудио в приложении — писк
  // сканера, сирена заказа, разблокировка звука на первом касании — уводит весь
  // звук страницы в громкий динамик до конца сеанса. Убрать их все я пробовал:
  // звук начинает захлёбываться, и это хуже, чем громкая связь.
  //
  // Пока трубка не доказана на живом телефоне, кнопки нет: мёртвая кнопка хуже
  // отсутствующей, а половина работающей функции хуже честного её отсутствия.
  // Проверяется отдельной страницей, не трогая приложение: vendor/audio-test.html
  AmbarCall.prototype.canRoute = function () {
    return false;
  };

  // Ищем среди выходов тот, что у уха. Имена системные и разноязыкие, поэтому
  // смотрим по нескольким корням сразу.
  AmbarCall.prototype._findEar = function () {
    var self = this;
    var a = document.createElement('audio');
    if (typeof a.setSinkId !== 'function' ||
        !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return Promise.resolve('');
    }
    return navigator.mediaDevices.enumerateDevices().then(function (list) {
      var выходы = list.filter(function (d) { return d.kind === 'audiooutput'; });
      var ear = выходы.filter(function (d) {
        return /earpiece|receiver|handset|трубк|ушн/i.test(d.label || '');
      })[0];
      self._earSink = ear ? ear.deviceId : '';
      // Список выходов — в лог. Если системная сессия не переключает, второй
      // рычаг это выбор выхода по имени, и решать, есть ли он, надо по факту.
      self._diag('выходы(' + выходы.length + '): ' +
        выходы.map(function (d) { return (d.label || 'без имени'); }).join(', ').slice(0, 120));
      return self._earSink;
    }).catch(function () { return ''; });
  };

  // Применить канал к системе. Отдельно от audioTo, потому что звать это надо
  // не только по нажатию: выставленный до первого звука канал НЕ применяется —
  // маршрутизировать системе нечего, и разговор начинается в громкой связи,
  // сколько бы раз мы ни просили трубку заранее. Отсюда и «первое нажатие
  // ничего не меняет»: состояние уже «трубка», а звук всё ещё в динамике.
  // Канал переключает ПЕРЕХОД, а не значение — вот что стоило понять сразу.
  //
  // Система меняет выход, когда тип звуковой сессии МЕНЯЕТСЯ у живого звука.
  // Записать туда то же самое, что уже стоит, — не переход: изменения нет,
  // переключать нечего. Отсюда всё поведение, которое видел человек: трубка,
  // выставленная до первого звука, не включалась никогда, а нажатие кнопки
  // включало — потому что нажатие и было единственным настоящим переходом.
  // Когда я начал выставлять трубку заранее, я убил и его: к моменту нажатия
  // там уже стояло то же значение.
  //
  // Поэтому: до первого звука сессию не трогаем вовсе, а трубку включаем
  // всегда переходом — сначала обычная, следом разговорная.
  // Куда идёт голос: в трубку у уха или в громкий динамик.
  //
  // Рычаг ровно один — выбор устройства вывода. Тип звуковой сессии отсюда
  // убран совсем: он не переключал ничего ни разу, зато сам уводил звук в
  // громкий динамик, а уведя — оставлял там всё до конца сеанса.
  //
  // Устройство называется НОВОМУ элементу, ещё ничего не игравшему: игравший
  // отвечает «готово» и не переключает. И только потом в него переезжает голос.
  AmbarCall.prototype._applyRoute = function (why) {
    var self = this;
    if (!this.canRoute() || !this._earSink) return;
    this._routedAt = Date.now();
    this._swapSink(this.route === 'ear' ? this._earSink : '').then(function (ok) {
      self._sayRoute(why, ok ? 'устройство названо' : 'устройство не принято');
    });
  };

  AmbarCall.prototype._sayRoute = function (why, как) {
    this._diag('канал: ' + (this.route === 'ear' ? 'ухо' : 'динамик') +
               ' · ' + (why || '') + ' · ' + как +
               ' · элемент=' + (this.audio ? this.audio.tagName : 'нет') +
               '/' + (this.audio && this.audio.paused ? 'пауза' : 'играет'));
  };

  AmbarCall.prototype.audioTo = function (where, тихо) {
    var self = this, ear = where !== 'speaker';
    var менялось = this.route !== (ear ? 'ear' : 'speaker');
    this.route = ear ? 'ear' : 'speaker';
    this._applyRoute('нажали');
    // Смена сессии перезапускает звук на уровне системы, и элемент после неё
    // остаётся на паузе — молча, ни ошибки, ни события. Возвращаем звук; сам
    // канал при этом не трогаем, переход уже идёт.
    this._resume();
    setTimeout(function () { self._resume(); }, 400);
    if (тихо && !менялось) return this.route;
    // Экран нужен глазам только там, где на него смотрят. В голосовом разговоре
    // у уха он обязан гаснуть сам — так щекой ничего и не нажать.
    this._keepAwake(this.video || !ear);
    this._emit('route', {to: this.route, real: this.canRoute()});
    return this.route;
  };

  // Вернуть звук после того, как система его прервала: смена сессии, чужой
  // системный звук, переключение выхода. Просто просим играть дальше — судить
  // о том, получилось ли, тут нельзя, для этого есть отдельный счётчик отказов.
  AmbarCall.prototype._resume = function () {
    var self = this, a = this.audio;
    // Пока звука собеседника нет, играть нечего и судить не о чем.
    if (!a || !a.srcObject) return;
    // Играющий элемент не трогаем: каждый запуск заставляет систему настроить
    // выход заново и перебивает выбранный канал.
    if (!a.paused) return;
    try {
      var p = a.play();
      if (p && p.catch) p.catch(function () {});
    } catch (e) {}
  };

  // Автоматического «а давайте вернём громкую связь» здесь больше нет, и это
  // осознанно. Признака «в трубку не пошло» у страницы не существует: смена
  // звуковой сессии сама на мгновение ставит элемент на паузу, а прямой отказ
  // системы приходит и на обычном переключении. Обе догадки уже были и обе
  // сделали хуже — сначала разговор уходил в громкую сам, потом на трубку
  // нельзя было переключиться вовсе, потому что каждое нажатие тут же
  // отменялось. Не слышно в трубке — человек нажмёт кнопку сам, это одно
  // касание; отменять его выбор за него нельзя.

  // Что этот телефон вообще умеет. Уходит один раз при входе и попадает в лог
  // сервера: гадать о чужом устройстве по памяти — то, за что уже досталось.
  AmbarCall.prototype._env = function () {
    var e = {};
    try {
      e.aus = !!(navigator.audioSession && 'type' in navigator.audioSession);
      e.ear = !!this._earSink;
      e.sink = typeof (document.createElement('audio').setSinkId) === 'function';
      e.wake = !!navigator.wakeLock;
      e.touch = matchMedia('(pointer: coarse)').matches;
      e.w = Math.min(screen.width, screen.height);
      var w = window.Telegram && Telegram.WebApp;
      e.tg = (w && w.platform) || '';
      e.ver = (w && w.version) || '';
      e.rot = !!(w && typeof w.lockOrientation === 'function');
    } catch (err) {}
    return e;
  };

  // Телефон это или планшет с компьютером. У трубки есть динамик у уха, у них
  // нет — и по умолчанию там громкая связь.
  AmbarCall.prototype.isPhone = function () {
    try {
      var узкий = Math.min(screen.width, screen.height) <= 500;
      var пальцем = matchMedia('(pointer: coarse)').matches;
      var tg = (window.Telegram && Telegram.WebApp && Telegram.WebApp.platform) || '';
      if (tg === 'tdesktop' || tg === 'macos' || tg === 'web') return false;
      return пальцем && узкий;
    } catch (e) { return false; }
  };

  // Камеру на входящем включаем, только если разрешение уже давали. Иначе
  // системное окно выскочит поверх звенящего звонка — ровно тогда, когда
  // человеку надо нажать «ответить», а не читать вопросы.
  AmbarCall.prototype.camGranted = function () {
    try { return localStorage.getItem('ambar_cam_ok') === '1'; } catch (e) { return false; }
  };
  // Горит ли своя камера прямо сейчас. Единственный источник правды: набор
  // включает её раньше, чем сервер отвечает «звоним», и экран, который в этот
  // момент выставляет своё представление о камере, затирает уже случившееся.
  AmbarCall.prototype.camLive = function () {
    return !!(this.cam && this.cam.getVideoTracks().some(function (t) {
      return t.readyState === 'live' && !t.muted;
    }));
  };
  AmbarCall.prototype.micLive = function () {
    return !!(this.stream && this.stream.getAudioTracks().some(
      function (t) { return t.readyState === 'live'; }));
  };

  // ── сокет ────────────────────────────────────────────────────────────────
  AmbarCall.prototype.connect = function () {
    if (this.ws && this.ws.readyState <= 1) return;
    this._closing = false;
    var url = this.api.replace(/^http/, 'ws') + '/api/call/ws';
    var self = this;
    var ws;
    try { ws = new WebSocket(url); } catch (e) { this._retryLater(); return; }
    this.ws = ws;

    ws.onopen = function () {
      ws.send(JSON.stringify({t: 'auth', tma: self.initData, as: self.as,
                              env: self._env()}));
    };
    ws.onmessage = function (ev) {
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      self._onMessage(m);
    };
    ws.onclose = function () {
      self.ws = null;
      if (self.call) self._teardown('link');
      self._emit('offline');
      if (!self._closing) self._retryLater();
    };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  };

  AmbarCall.prototype._retryLater = function () {
    // Растущая пауза: смена длинная, а связь у водителя пропадает в лифте и в
    // подземном паркинге по десять раз за вечер.
    var wait = Math.min(30000, 1000 * Math.pow(2, this._retry++));
    var self = this;
    setTimeout(function () { self.connect(); }, wait);
  };

  AmbarCall.prototype.close = function () {
    this._closing = true;
    this.hangup();
    this._freeMic();
    if (this.ws) { try { this.ws.close(); } catch (e) {} }
  };

  AmbarCall.prototype._send = function (m) {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(m));
  };

  AmbarCall.prototype._onMessage = function (m) {
    switch (m.t) {
      case 'ready':
        this._retry = 0;
        this.me = m.me;
        this.kind = m.kind || '';
        this.roster = m.roster || [];
        this.recent = m.recent || [];
        this.defaultTarget = m.peer || '';
        if (m.ice && m.ice.length) this.ice = m.ice;
        this._emit('ready', m);
        break;

      case 'roster':
        this.roster = m.roster || [];
        this._emit('roster', {roster: this.roster});
        break;

      case 'recent':
        this.recent = m.recent || [];
        this._emit('recent', {recent: this.recent});
        break;

      case 'denied':
        this._emit('denied');
        break;

      case 'ring':                                  // нам звонят
        this.call = {id: m.call, peer: m.frm, dir: 'in', order: m.order || '',
                     kind: m.kind || '', video: !!m.video};
        this.tones.ring();
        this._emit('ring', this.call);
        break;

      case 'calling':                               // звоним мы
        this.call = {id: m.call, peer: m.to, dir: 'out', order: '', note: m.note || '',
                     video: this.video};
        this.tones.ringback();
        this._emit('calling', this.call);
        break;

      case 'accepted':                              // нашу трубку сняли
        if (!this.call || this.call.id !== m.call) break;
        this.tones.release();
        this.call.peer = m.by || this.call.peer;
        this._emit('talking', this.call);
        this._startMedia(true);
        break;

      case 'joined':                                // мы сняли трубку
        if (!this.call || this.call.id !== m.call) break;
        this.tones.release();
        this._emit('talking', this.call);
        this._startMedia(false);
        break;

      case 'cancel':                                // взяли на другом устройстве
        if (this.call && this.call.id === m.call) this._teardown('cancel');
        break;

      case 'failed':
        // «Занят» приходит в ответ на попытку набрать во время разговора, и
        // разговор при этом жив. Обнулить call здесь значит потерять трубку:
        // класть будет нечего, а голос продолжит идти.
        if (m.why === 'busy' && this.call) {
          this._emit('failed', {why: m.why, peer: m.peer || '', keep: true});
          break;
        }
        this.call = null;
        this.tones.stop();
        // Набор успел включить камеру и микрофон до того, как выяснилось, что
        // звонок не пройдёт. Гасим: иначе после «занято» глазок камеры горит
        // дальше, хотя никакого звонка нет.
        this._camOff();
        this.video = false;
        if (this.stream) {
          this.stream.getAudioTracks().forEach(function (t) { t.enabled = false; });
        }
        if (m.why === 'busy_them') this.tones.busy(); else this.tones.fail();
        this._emit('failed', {why: m.why, peer: m.peer || ''});
        break;

      case 'end':
        if (this.call && this.call.id === m.call) {
          if (m.say) this.call.say = m.say;
          this._teardown(m.why || 'end');
        }
        break;

      case 'camstate':                              // собеседник включил или выключил камеру
        this._emit('remotecam', {on: !!m.on});
        break;

      case 'sdp':
        this._onSdp(m.data);
        break;

      case 'ice':
        this._onIce(m.data);
        break;
    }
  };

  // ── действия ─────────────────────────────────────────────────────────────
  AmbarCall.prototype.dial = function (toKey, order, video) {
    var self = this;
    this.video = !!video;
    // Гудок — сразу по нажатию, а не после ответа сервера. Между нажатием и
    // ответом лежит запрос микрофона с камерой и полный оборот до сервера:
    // секунда, а то и две полной тишины, в которую человек успевает решить,
    // что ничего не работает. Не прошло — тишину сменит короткий отбойный тон.
    this.tones.prime();
    this.tones.ringback();
    return this._mic().then(function () {
      return self.video ? self._camOn() : null;
    }).then(function () {
      self._send({t: 'call', to: toKey || self.defaultTarget || '',
                  order: order || '', video: self.video});
    }).catch(function (e) {
      // Не дали микрофон — звонка не будет, и гудеть в пустоту нельзя.
      self.tones.stop();
      throw e;
    });
  };

  AmbarCall.prototype.accept = function () {
    if (!this.call) return Promise.resolve();
    var self = this, id = this.call.id;
    this.tones.prime();
    this.video = !!this.call.video;
    return this._mic().then(function () {
      return self.video ? self._camOn() : null;
    }).then(function () { self._send({t: 'accept', call: id}); })
      .catch(function (e) {
        // Камеры может не быть или её могут не дать — звонок от этого умирать
        // не должен: продолжаем голосом.
        if (self.video) { self.video = false; self._emit('novideo', {}); }
        self._send({t: 'accept', call: id});
      });
  };

  // Отклонить можно молча или словом. Слово короткое и заранее заготовленное:
  // за рулём набирать нечего, а «занят» и «перезвоню» закрывают почти всё.
  AmbarCall.prototype.reject = function (say) {
    if (!this.call) return;
    this._send({t: 'reject', call: this.call.id, say: say || ''});
    // Своё «отклонить» — не «вам отказали»: ни отбойного тона, ни всплывающей
    // строки «Отклонён» человеку, который сам только что нажал отбой.
    this._teardown('declined');
  };

  AmbarCall.prototype.hangup = function () {
    if (!this.call) return;
    this._send({t: 'bye', call: this.call.id});
    this._teardown('hangup');
  };

  AmbarCall.prototype.mute = function (on) {
    if (!this.stream) return false;
    this.stream.getAudioTracks().forEach(function (t) { t.enabled = !on; });
    return !!on;
  };

  AmbarCall.prototype.muted = function () {
    return !!(this.stream && this.stream.getAudioTracks().some(
      function (t) { return !t.enabled; }));
  };

  // ── микрофон ─────────────────────────────────────────────────────────────
  // Отдельная кнопка «разрешить» на экране связи зовёт сюда же: разрешение
  // спрашивается в спокойный момент, а не когда телефон уже звонит.
  // Спрашиваем СРАЗУ и микрофон, и камеру — одним окном. Раньше микрофон
  // просили здесь, а камеру потом, при первом видеозвонке: человек видел два
  // разных окна в разное время и справедливо считал, что его переспрашивают.
  // Видеодорожку тут же гасим — камера между звонками гореть не должна, но
  // разрешение на неё в этом сеансе уже получено и больше не спросится.
  AmbarCall.prototype.warmMic = function () {
    var self = this;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this._emit('nomic', {why: 'unsupported'});
      return Promise.reject(new Error('no getUserMedia'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true},
      video: {facingMode: 'user'}
    }).then(function (s) {
      s.getVideoTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      var only = new MediaStream(s.getAudioTracks());
      self.stream = only;
      self._prepAudio();
      try { localStorage.setItem('ambar_mic_ok', '1'); } catch (e) {}
      try { localStorage.setItem('ambar_cam_ok', '1'); } catch (e) {}
      self._emit('mic', {ok: true});
      return only;
    }).catch(function () {
      // Камеру могли не дать, а микрофон дать — это рабочий случай, звонок
      // голосом должен состояться.
      return self._mic();
    });
  };

  AmbarCall.prototype._mic = function () {
    var self = this;
    if (this.micLive()) {                      // уже есть — просто включаем
      this.stream.getAudioTracks().forEach(function (t) { t.enabled = true; });
      return Promise.resolve(this.stream);
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this._emit('nomic', {why: 'unsupported'});
      return Promise.reject(new Error('no getUserMedia'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}
    }).then(function (s) {
      self.stream = s;
      try { localStorage.setItem('ambar_mic_ok', '1'); } catch (e) {}
      self._prepAudio();
      // Имена устройств вывода видны только после разрешения микрофона —
      // значит искать трубку надо здесь, задолго до первого звонка.
      self._findEar();
      self._emit('mic', {ok: true});
      return s;
    }).catch(function (err) {
      var why = (err && err.name) || 'error';
      if (why === 'NotAllowedError') { try { localStorage.removeItem('ambar_mic_ok'); } catch (e) {} }
      self._emit('nomic', {why: why});
      throw err;
    });
  };

  // Кто в соединении отправляет картинку.
  //
  // Искать его по своей дорожке нельзя, и это была не мелочь: выключение
  // камеры ставит отправителю пустую дорожку, поиск после этого не находит
  // ничего, и включение добавляло в соединение ВТОРУЮ картинку вместо
  // возврата первой. Соединение при этом надо пересобирать, а пересобирать
  // его некому — камера загоралась на своём экране и больше никогда не
  // доходила до собеседника. Насмерть, до конца разговора.
  //
  // Место для картинки в соединении одно и никуда не девается, даже когда
  // дорожки в нём нет. По нему и ищем.
  AmbarCall.prototype._vsender = function () {
    var pc = this.pc;
    if (!pc) return null;
    if (pc.getTransceivers) {
      var t = pc.getTransceivers().find(function (x) {
        return (x.sender && x.sender.track && x.sender.track.kind === 'video')
            || (x.receiver && x.receiver.track && x.receiver.track.kind === 'video');
      });
      if (t && t.sender) return t.sender;
    }
    return pc.getSenders().find(function (x) {
      return x.track && x.track.kind === 'video';
    }) || null;
  };

  // Камера живёт только на время разговора — в отличие от микрофона её держать
  // между звонками нельзя: горящий глазок весь день пугает не зря.
  AmbarCall.prototype._camOn = function (facing) {
    var self = this;
    // Камера уже горит — второй раз её просить нельзя: у системы это новый
    // поток, старый при этом останется гореть навсегда. Так бывает, когда на
    // входящем показали себя, а потом сняли трубку: включение идёт дважды.
    var cur = !facing && this.cam && this.cam.getVideoTracks()[0];
    if (cur && cur.readyState === 'live') {
      var sn = this._vsender();
      if (sn && sn.track !== cur) { sn.replaceTrack(cur); this._capVideo(); }
      this.video = true;
      this._emit('cam', {on: true, facing: this.facing, stream: this.cam});
      this._send({t: 'camstate', on: true});
      return Promise.resolve(this.cam);
    }
    this.facing = facing || this.facing || 'user';
    return navigator.mediaDevices.getUserMedia({
      video: {facingMode: this.facing, width: {ideal: 640}, height: {ideal: 480},
              frameRate: {ideal: 20, max: 24}}
    }).then(function (s) {
      var track = s.getVideoTracks()[0];
      if (!track) throw new Error('no camera');
      self.cam = s;
      self.video = true;
      // Уход из телеграма в другое приложение разговор не рвёт — звук идёт
      // дальше, — но съёмку система останавливает, и делает это молча.
      // Собеседник в этот момент видит, как картинка просто пропала: ни
      // замершего кадра, ни перечёркнутой камеры, ничего.
      //
      // Своя дорожка про это честно сообщает (mute/unmute), в отличие от
      // чужой, на которую полагаться нельзя. Поэтому слушаем свою и говорим
      // собеседнику словами — теми же словами, что и при нажатии кнопки.
      track.onmute = track.onunmute = function () {
        var live = track.readyState === 'live' && !track.muted;
        self._send({t: 'camstate', on: live});
        self._emit('cam', {on: live, facing: self.facing});
      };
      track.onended = function () {
        self._send({t: 'camstate', on: false});
        self._emit('cam', {on: false, facing: self.facing});
      };
      // Соединение уже собрано — просто подменяем дорожку, без пересборки.
      var sender = self._vsender();
      if (sender) { sender.replaceTrack(track); self._capVideo(); }
      else if (self.pc) { self.pc.addTrack(track, self.stream || s); self._capVideo(); }
      self._emit('cam', {on: true, facing: self.facing, stream: s});
      self._send({t: 'camstate', on: true});
      return s;
    });
  };

  AmbarCall.prototype._camOff = function () {
    if (!this.cam) return;
    this.cam.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    this.cam = null;
    var sender = this._vsender();
    if (sender) { try { sender.replaceTrack(null); } catch (e) {} }
    // Говорим собеседнику словами. На замолкание дорожки полагаться нельзя:
    // после replaceTrack(null) у него просто перестают идти кадры, событие
    // приходит не во всех браузерах, и на экране висит замерший кадр.
    this._send({t: 'camstate', on: false});
    this._emit('cam', {on: false});
  };

  // Выключить и включить свою камеру посреди разговора.
  AmbarCall.prototype.camera = function (on) {
    if (on) return this._camOn().catch(function () {});
    this._camOff();
    return Promise.resolve();
  };

  // Переднюю на заднюю и обратно: показать полку или подъезд удобнее задней.
  AmbarCall.prototype.flip = function () {
    var next = this.facing === 'user' ? 'environment' : 'user';
    var old = this.cam;
    var self = this;
    return this._camOn(next).then(function (s) {
      if (old && old !== s) old.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      return s;
    }).catch(function () { self.facing = self.facing === 'user' ? 'environment' : 'user'; });
  };

  // Потолок битрейта картинки. Без него видео на 4G съедает канал и первым
  // страдает голос — а голос в работе важнее.
  AmbarCall.prototype._capVideo = function () {
    var pc = this.pc;
    if (!pc) return;
    pc.getSenders().forEach(function (s) {
      if (!s.track || s.track.kind !== 'video' || !s.getParameters) return;
      try {
        var p = s.getParameters();
        p.encodings = p.encodings && p.encodings.length ? p.encodings : [{}];
        p.encodings[0].maxBitrate = VIDEO_MAX_KBPS * 1000;
        p.encodings[0].maxFramerate = 24;
        s.setParameters(p);
      } catch (e) {}
    });
  };

  // Элемент воспроизведения создаём внутри касания: айфон пускает звук только
  // у того элемента, чей play() случился по жесту человека.
  // Элемент, которым играет голос собеседника.
  //
  // Именно audio, а не video: айфон считает видео обычным воспроизведением и
  // ведёт его в громкий динамик, а ушной оставляет разговорному звуку. И
  // остаётся в разметке видимым в один пиксель — спрятанному display:none
  // система фонового звука не даёт.
  AmbarCall.prototype._makeAudio = function () {
    var self = this;
    var a = document.createElement('audio');
    a.autoplay = true; a.muted = false; a.volume = 1;
    a.setAttribute('playsinline', '');
    a.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;' +
                      'opacity:.01;pointer-events:none;z-index:-1';
    // Каждый запуск звука система сопровождает своей настройкой выхода, значит
    // после перерыва канал надо ставить заново. Но не чаще раза в две секунды,
    // иначе на возвратах получается кружение.
    a.addEventListener('playing', function () {
      if (!self.call || self.audio !== a) return;
      var t = Date.now();
      if (self._routedAt && t - self._routedAt < 2000) return;
      self._applyRoute(self._routedAt ? 'звук вернулся' : 'пошёл звук');
    });
    // Система прерывает воспроизведение сама: чужой сигнал, смена выхода,
    // перерыв. Элемент после этого сам не возвращается — возвращаем мы, иначе
    // разговор беззвучно умирает на ровном месте. Канал отсюда не трогаем.
    a.addEventListener('pause', function () {
      if (!self.call || self.audio !== a || !a.srcObject) return;
      setTimeout(function () {
        if (self.call && self.audio === a && a.srcObject && a.paused) self._resume();
      }, 120);
    });
    document.body.appendChild(a);
    return a;
  };

  AmbarCall.prototype._prepAudio = function () {
    if (!this.audio) this.audio = this._makeAudio();
    try { this.audio.play().catch(function () {}); } catch (e) {}
  };

  // Смена выхода — на НОВОМ элементе, и это главное открытие всей истории.
  //
  // Замер на самом телефоне показал: выбор устройства работает и звук
  // действительно уходит в трубку — но только пока элемент ещё ничего не
  // играл. Стоит перезагрузить страницу, и тот же вызов уже ничего не
  // переключает, отвечая при этом «готово». Обновление помогало не всегда, а
  // помогал заново открытый адрес — то есть свежий, ничего не игравший вывод.
  //
  // Отсюда и «первые два звонка работали, потом перестало»: элемент у нас
  // один на весь сеанс, и после первого разговора он переставал слушаться.
  //
  // Поэтому теперь на каждое переключение делается новый элемент: сначала ему
  // называется устройство — молчащему, ещё без потока, — и только потом в него
  // кладётся голос.
  AmbarCall.prototype._swapSink = function (deviceId) {
    var self = this, старый = this.audio;
    var поток = старый && старый.srcObject;
    if (!поток) return Promise.resolve(false);      // играть нечего — менять нечего
    var a = this._makeAudio();
    var назвать = (typeof a.setSinkId === 'function')
      ? a.setSinkId(deviceId || '') : Promise.resolve();
    return назвать.then(function () { return true; })
      .catch(function (e) { self._sayRoute('', 'устройство не принято: ' + ((e && e.name) || '?')); return false; })
      .then(function (ok) {
        self.audio = a;
        a.srcObject = поток;
        if (старый) {
          try { старый.pause(); старый.srcObject = null; старый.remove(); } catch (e) {}
        }
        return a.play().then(function () { return ok; }).catch(function () { return ok; });
      });
  };

  AmbarCall.prototype._freeMic = function () {
    if (!this.stream) return;
    this.stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    this.stream = null;
    this._emit('mic', {ok: false});
  };

  // ── соединение ───────────────────────────────────────────────────────────
  AmbarCall.prototype._startMedia = function (isCaller) {
    var self = this;
    var pc = new RTCPeerConnection({iceServers: this.ice});
    this.pc = pc;
    this._pendingIce = [];
    this._isCaller = isCaller;

    if (this.stream) {
      this.stream.getAudioTracks().forEach(function (t) {
        t.enabled = true;
        pc.addTrack(t, self.stream);
      });
    } else {
      // Микрофон не дали, но слышать собеседника человек всё равно должен, и
      // соединение обязано собраться. Без этого разговор просто рвался бы на
      // ровном месте: своей дорожки нет, добавлять нечего, дальше исключение.
      try { pc.addTransceiver('audio', {direction: 'recvonly'}); } catch (e) {}
    }
    if (this.cam) this.cam.getVideoTracks().forEach(function (t) { pc.addTrack(t, self.cam); });

    pc.ontrack = function (e) {
      // Дорожку могут прислать и без потока — тогда собираем поток сами.
      // Раньше такой звонок молча оставался без звука: выходили отсюда сразу,
      // и голос собеседника было некуда положить.
      var st = (e.streams && e.streams[0]) || new MediaStream([e.track]);
      if (!st) return;
      self.remote = st;
      // Звук всегда в свой элемент: видео может быть выключено, а слышать надо.
      if (self.audio && e.track.kind === 'audio') {
        self.audio.srcObject = st;
        // Только запускаем. Канал переключит событие «пошёл звук» — приход
        // дорожки ещё не значит, что её слышно, а переключать системе нечего,
        // пока звук не идёт на самом деле.
        self._resume();
      }
      if (e.track.kind === 'video') {
        self.video = true;
        // Собеседник может выключить камеру посреди разговора — дорожка при
        // этом не исчезает, она «замолкает». Ловим это, чтобы вернуть
        // голосовой вид, а не показывать замерший кадр.
        var t = e.track;
        var say = function () { self._emit('remotecam', {on: !t.muted && t.readyState === 'live'}); };
        t.onmute = say; t.onunmute = say; t.onended = say;
        setTimeout(say, 300);
      }
      self._emit('stream', {stream: st, kind: e.track.kind});
    };
    pc.onicecandidate = function (e) {
      if (e.candidate) self._send({t: 'ice', data: e.candidate});
    };
    pc.onconnectionstatechange = function () {
      var st = pc.connectionState;
      self._emit('net', {state: st});
      if (st === 'connected') {
        clearTimeout(self._iceRestartT);
        self._quality('good');
      }
      // Разрыв в тоннеле или в лифте — не повод класть трубку. Даём связи
      // собраться заново и только потом сдаёмся.
      if (st === 'disconnected') {
        self._quality('bad');
        clearTimeout(self._iceRestartT);
        self._iceRestartT = setTimeout(function () { self._restartIce(); }, ICE_RESTART_AFTER);
      }
      if (st === 'failed') {
        self._quality('bad');
        self._restartIce();
      }
    };

    this._watchQuality();
    // Ищем трубку у уха заранее: к моменту, когда человек нажмёт «динамик»,
    // ответ уже должен быть.
    // Второй раз список не перебираем: он уже собран, когда брали микрофон.
    if (this._earSink) {
      this._emit('route', {to: this.route, real: this.canRoute()});
    } else {
      this._findEar().then(function () {
        self._emit('route', {to: self.route, real: self.canRoute()});
        // Поиск мог закончиться уже после начала разговора — тогда канал
        // ставим заново, теперь уже прямым выбором выхода.
        if (self.call && self._earSink) self._applyRoute('нашли выход');
      });
    }
    // Голос в трубку у уха — экран не нужен, пусть гаснет сам: это и есть
    // защита от случайного нажатия щекой, и никакая накладка её не заменит.
    // Выбор запоминаем сразу, чтобы кнопка с первой секунды показывала правду,
    // а вот саму сессию не трогаем: до первого звука переключать нечего, и
    // тронутая заранее, она потом не даёт сделать настоящий переход.
    this._routedAt = 0;
    this.route = this.route || (this.isPhone() && !this.video ? 'ear' : 'speaker');
    this._keepAwake(this.video || this.route === 'speaker');
    this._emit('route', {to: this.route, real: this.canRoute()});
    // Сразу сообщаем, есть ли у нас картинка: собеседник должен знать это с
    // первой секунды, а не после первого переключения.
    this._send({t: 'camstate', on: !!this.cam});

    this._capVideo();

    if (isCaller) {
      pc.createOffer().then(function (o) { return pc.setLocalDescription(o); })
        .then(function () { self._send({t: 'sdp', data: pc.localDescription}); })
        .catch(function (e) { console.warn('[call] offer', e); });
    }
  };

  // Пересборка связи без потери разговора: та же сессия, новые кандидаты.
  AmbarCall.prototype._restartIce = function () {
    var pc = this.pc, self = this;
    if (!pc || !this._isCaller || pc.connectionState === 'connected') return;
    try {
      pc.createOffer({iceRestart: true})
        .then(function (o) { return pc.setLocalDescription(o); })
        .then(function () { self._send({t: 'sdp', data: pc.localDescription}); })
        .catch(function () {});
    } catch (e) {}
  };

  AmbarCall.prototype._onSdp = function (sdp) {
    if (!this.pc || !sdp) return;
    var self = this;
    this.pc.setRemoteDescription(sdp).then(function () {
      // Кандидаты умеют обгонять описание — те, что пришли раньше, ждали здесь.
      self._pendingIce.forEach(function (c) {
        self.pc.addIceCandidate(c).catch(function () {});
      });
      self._pendingIce = [];
      if (sdp.type === 'offer') {
        return self.pc.createAnswer().then(function (a) {
          return self.pc.setLocalDescription(a);
        }).then(function () {
          self._send({t: 'sdp', data: self.pc.localDescription});
        });
      }
    }).catch(function (e) { console.warn('[call] sdp', e); });
  };

  AmbarCall.prototype._onIce = function (c) {
    if (!this.pc || !c) return;
    if (!this.pc.remoteDescription || !this.pc.remoteDescription.type) {
      this._pendingIce.push(c);
      return;
    }
    this.pc.addIceCandidate(c).catch(function () {});
  };

  // ── качество связи ───────────────────────────────────────────────────────
  // Водитель на 4G в подземном паркинге должен видеть, что связь плохая, а не
  // думать, что собеседник замолчал.
  AmbarCall.prototype._watchQuality = function () {
    var self = this;
    clearInterval(this._statsT);
    this._lastStats = null;
    this._statsT = setInterval(function () {
      if (!self.pc) return;
      self.pc.getStats().then(function (rep) {
        var inb = null;
        rep.forEach(function (r) { if (r.type === 'inbound-rtp' && r.kind === 'audio') inb = r; });
        if (!inb) return;
        var prev = self._lastStats;
        self._lastStats = {lost: inb.packetsLost || 0, got: inb.packetsReceived || 0,
                           jitter: inb.jitter || 0};
        if (!prev) return;
        var dLost = self._lastStats.lost - prev.lost;
        var dGot = self._lastStats.got - prev.got;
        if (dGot <= 0) { self._quality('bad'); return; }   // тишина в трубке
        var loss = dLost / (dLost + dGot);
        var jit = self._lastStats.jitter;
        self._quality(loss > 0.12 || jit > 0.12 ? 'bad'
                    : loss > 0.03 || jit > 0.05 ? 'weak' : 'good');
      }).catch(function () {});
    }, STATS_EVERY);
  };

  AmbarCall.prototype._quality = function (q) {
    if (this.quality === q) return;
    this.quality = q;
    this._emit('quality', {quality: q});
  };

  // Экран не должен гаснуть посреди разговора: телефон в руке у уха, а
  // погасший экран в вебвью означает остановленный звонок.
  AmbarCall.prototype._keepAwake = function (on) {
    var self = this;
    if (on) {
      try {
        if (navigator.wakeLock && !this._wake) {
          navigator.wakeLock.request('screen').then(function (w) {
            self._wake = w;
            // Систему просить дважды нельзя, а снимает она молча — поэтому
            // отпущенный замок сразу забываем, чтобы взять новый было можно.
            w.addEventListener('release', function () {
              if (self._wake === w) self._wake = null;
            });
          }).catch(function () {});
        }
      } catch (e) {}
    } else if (this._wake) {
      try { this._wake.release(); } catch (e) {}
      this._wake = null;
    }
  };

  AmbarCall.prototype._teardown = function (why) {
    this._routedAt = 0;
    this.tones.stop();
    // Конец разговора и несостоявшийся звонок звучат по-разному — как в
    // телефоне: короткий тон в конце разговора и отбойный, когда соединения
    // не вышло. Обрыв связи и отмена — молча, там и без тона всё сказано.
    if (why === 'hangup' || why === 'end') this.tones.bye();
    else if (why === 'no_answer' || why === 'rejected') this.tones.fail();
    // Прощальный тон короткий — контекст закрываем следом, чтобы он не висел
    // между звонками и не мешал звуку следующего.
    var tn = this.tones;
    setTimeout(function () { tn.release(); }, 900);
    clearInterval(this._statsT); this._statsT = null;
    clearTimeout(this._iceRestartT);
    this._keepAwake(false);
    if (this.pc) { try { this.pc.close(); } catch (e) {} this.pc = null; }
    // Микрофон НЕ останавливаем — только глушим. Следующий звонок не должен
    // снова упираться в системное окно.
    if (this.stream) {
      this.stream.getAudioTracks().forEach(function (t) { t.enabled = false; });
    }
    if (this.audio) { try { this.audio.srcObject = null; } catch (e) {} }
    // Камеру гасим всегда: в отличие от микрофона её нельзя оставлять включённой
    // между звонками — горящий глазок пугает и правильно делает.
    this._camOff();
    this.remote = null;
    this.video = false;
    this.route = '';
    // Тип звуковой сессии — один на всю страницу и переживает звонок. Оставить
    // его разговорным значит увести в трубку у уха ВЕСЬ звук приложения:
    // сирену нового заказа, вызов следующего звонка, любое уведомление.
    // Снаружи это выглядит как «звук пропал совсем» — телефон звучит, но
    // только если приложить его к уху. Возвращаем как было.
    var say = this.call && this.call.say;
    this.call = null;
    this._pendingIce = [];
    this.quality = '';
    this._emit('ended', {why: why, say: say || ''});
  };

  window.AmbarCall = AmbarCall;
})();
