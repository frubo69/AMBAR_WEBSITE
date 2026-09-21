// Подставная камера и распознаватель — ДО любых скриптов приложения.
// Камера — настоящий MediaStream с холста (видео играет, кадры идут), но
// getUserMedia считается: каждый вызов на андроиде — это запрос разрешения.
// Распознаватель отдаёт тот код, что стенд «держит в кадре» (window.__FRAME).
(function(){
  window.__ERR = [];
  window.addEventListener('error', e => __ERR.push({t: Date.now(), m: String(e.message || e.error), s: (e.error && e.error.stack || '').slice(0, 600)}));
  window.addEventListener('unhandledrejection', e => __ERR.push({t: Date.now(), m: 'unhandled: ' + String(e.reason && (e.reason.message || e.reason)), s: (e.reason && e.reason.stack || '').slice(0, 600)}));
  window.__CAM = {gum: 0, enum: 0, streams: [], label: 'Back Camera', deny: false};
  const cv = document.createElement('canvas'); cv.width = 640; cv.height = 480;
  const cx = cv.getContext('2d');
  let k = 0;
  setInterval(() => { k = (k + 1) % 255; cx.fillStyle = `rgb(${k},${255 - k},90)`; cx.fillRect(0, 0, 640, 480); }, 40);
  if(!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', {value: {}});
  navigator.mediaDevices.getUserMedia = async () => {
    __CAM.gum++;
    await new Promise(r => setTimeout(r, 30));
    if(__CAM.deny){ const e = new Error('Permission denied'); e.name = 'NotAllowedError'; throw e; }
    const s = cv.captureStream(25);
    const tr = s.getVideoTracks()[0];
    const label = __CAM.label;
    Object.defineProperty(tr, 'label', {get: () => label});
    tr.getCapabilities = () => ({torch: false});
    tr.getSettings = () => ({deviceId: /dual|wide/i.test(label) ? 'virtual' : 'cam-main'});
    __CAM.streams.push(s);
    return s;
  };
  navigator.mediaDevices.enumerateDevices = async () => {
    __CAM.enum++;
    return [{kind: 'videoinput', label: 'Back Camera', deviceId: 'cam-main'},
            {kind: 'videoinput', label: 'Back Ultra Wide Camera', deviceId: 'cam-uw'},
            {kind: 'videoinput', label: 'Front Camera', deviceId: 'cam-front'}];
  };
  window.__FRAME = null;
  window.jsQR = () => (window.__FRAME ? {data: window.__FRAME} : null);
})();
