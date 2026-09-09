// A/B: does resizing a decoding <video> flicker on Safari? (#335 residual)
//
// The Live page's preview-zoom.js sizes the picture by writing explicit
// left/top/width/height on the <video> (Fit / Fill / 1:1); the Preview
// component never loads it, so its video stays object-fit:contain and is never
// resized. This replays the same recorded HEVC via MSE and, in ?mode=resize,
// toggles the element's size the way preview-zoom does at known times; in
// ?mode=control it never resizes. Black frames are sampled and each one is
// tagged if it lands just after a resize, so the two runs are directly
// comparable. Verdict on window.__result (+ POST /result) for the driver.
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var mode = q.get('mode') === 'resize' ? 'resize' : 'control';
  var maxMs = +(q.get('ms') || 24000);
  var base = q.get('stream') || 'stream';
  var BATCH = 5;                       // coalesced append, matching the #411 fix

  var R = {
    ua: navigator.userAgent, mode: mode, codec: null,
    appended: 0, fragments: 0, decodeErrors: 0, errorCodes: [],
    resizes: [], blackFrames: 0, blackEvents: 0, resizeFlickers: 0,
    otherBlackEvents: 0, maxCurrentTime: 0, playedSeconds: 0,
    samples: [], note: '', reproduced: null, done: false
  };
  window.__progress = R;
  function post(done) {
    R.done = !!done;
    var body = JSON.stringify(R);
    try { if (navigator.sendBeacon) navigator.sendBeacon('/result', body);
      else fetch('/result', { method: 'POST', body: body, keepalive: true }); } catch (e) {}
    if (done) { window.__result = R; document.title = 'DONE'; }
    try { document.getElementById('log').textContent =
      JSON.stringify({ mode: R.mode, t: R.maxCurrentTime.toFixed(2),
        resizes: R.resizes.length, resizeFlickers: R.resizeFlickers,
        otherBlackEvents: R.otherBlackEvents, note: R.note.slice(-160) }, null, 1); } catch (e) {}
  }

  var video = document.getElementById('v');
  var stage = document.getElementById('stage');
  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d', { willReadFrequently: true });

  // preview-zoom's three modes, as element geometry over the fixed stage.
  function applyView(name) {
    var sw = stage.clientWidth, sh = stage.clientHeight;
    var fw = video.videoWidth || 2592, fh = video.videoHeight || 1520;
    var scale = name === 'fit' ? Math.min(sw / fw, sh / fh)
      : name === 'fill' ? Math.max(sw / fw, sh / fh)
      : 1;                                   // '1:1'
    var pw = fw * scale, ph = fh * scale;
    var s = video.style;
    s.inset = 'auto';
    s.objectFit = 'fill';                     // we size the element to the picture
    s.left = ((sw - pw) / 2).toFixed(1) + 'px';
    s.top = ((sh - ph) / 2).toFixed(1) + 'px';
    s.width = pw.toFixed(1) + 'px';
    s.height = ph.toFixed(1) + 'px';
    R.resizes.push({ t: +R.maxCurrentTime.toFixed(2), view: name, w: Math.round(pw), h: Math.round(ph) });
    R.lastResizeWall = performance.now();
  }

  function luma() {
    try {
      if (!video.videoWidth) return null;
      canvas.width = 64; canvas.height = 36;
      ctx.drawImage(video, 0, 0, 64, 36);
      var d = ctx.getImageData(0, 0, 64, 36).data, s = 0;
      for (var i = 0; i < d.length; i += 4) s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      return s / (d.length / 4);
    } catch (e) { return null; }
  }

  // --- MSE replay (coalesced, muted, gesture-started) ---
  window.__play = function () { try { video.muted = true; var p = video.play(); if (p && p.catch) p.catch(function () {}); } catch (e) {} };
  document.addEventListener('click', window.__play, true);
  video.addEventListener('canplay', window.__play);

  var man, buf, initSeg, frags = [], fragIdx = 0, ms, sb;
  Promise.all([
    fetch(base + '.json').then(function (r) { return r.json(); }),
    fetch(base + '.bin').then(function (r) { return r.arrayBuffer(); })
  ]).then(function (res) {
    man = res[0]; buf = res[1]; R.codec = man.codecString; R.fragments = man.fragments.length;
    initSeg = buf.slice(0, man.initLength);
    frags = man.fragments.map(function (f) { return buf.slice(f.offset, f.offset + f.length); });
    setupMS(); start();
  }).catch(function (e) { R.note += 'load-fail:' + e + ' | '; post(true); });

  function setupMS() {
    ms = new MediaSource();
    video.src = URL.createObjectURL(ms);
    ms.addEventListener('sourceopen', function () {
      try { sb = ms.addSourceBuffer(man.mime); } catch (e) { R.note += 'addSB:' + e; return post(true); }
      appendOnce(initSeg, function () { window.__play(); pump(); });
    }, { once: true });
  }
  function appendOnce(data, cb) {
    function end() { sb.removeEventListener('updateend', end); cb && cb(); }
    sb.addEventListener('updateend', end);
    try { sb.appendBuffer(data); } catch (e) { sb.removeEventListener('updateend', end); setTimeout(cb, 40); }
  }
  function bufferedAhead() { try { var b = video.buffered; if (b && b.length) return b.end(b.length - 1) - video.currentTime; } catch (e) {} return 0; }
  function pump() {
    if (window.__done || fragIdx >= frags.length) { if (fragIdx >= frags.length) { try { ms.endOfStream(); } catch (e) {} } return; }
    if (!sb || ms.readyState !== 'open' || sb.updating) { setTimeout(pump, 20); return; }
    try { var b = video.buffered; if (b.length && b.start(0) < video.currentTime - 4) { sb.remove(0, video.currentTime - 2); setTimeout(pump, 30); return; } } catch (e) {}
    if (bufferedAhead() > 8) { setTimeout(pump, 100); return; }
    var n = Math.min(BATCH, frags.length - fragIdx), total = 0, i;
    for (i = 0; i < n; i++) total += frags[fragIdx + i].byteLength;
    var out = new Uint8Array(total), off = 0;
    for (i = 0; i < n; i++) { out.set(new Uint8Array(frags[fragIdx + i]), off); off += frags[fragIdx + i].byteLength; }
    appendOnce(out.buffer, function () { R.appended += n; fragIdx += n; pump(); });
  }

  function start() {
    video.addEventListener('error', function () {
      var c = video.error ? video.error.code : -1; R.decodeErrors++; R.errorCodes.push(c);
    });

    // The resize schedule: only in resize mode. Real preview-zoom view changes.
    var schedule = ['fill', 'one', 'fit', 'fill', 'one', 'fit'];
    var si = 0;
    var resizeTimer = setInterval(function () {
      if (window.__done) return;
      if (mode === 'resize' && video.videoWidth && si < schedule.length) {
        applyView(schedule[si++]);
      }
    }, 2500);

    var lastBlack = false;
    var sampler = setInterval(function () {
      var t = video.currentTime;
      if (t > R.maxCurrentTime + 0.001) R.maxCurrentTime = t;
      var b = luma();
      if (b !== null) {
        // The recorded field is a flat ~73 (lensless), so a dip below 30 is a
        // clear flicker (black or near-black re-decode), not scene content.
        var isBlack = b < 30;
        if (isBlack) R.blackFrames++;
        if (isBlack && !lastBlack) {
          R.blackEvents++;
          // Tag it if it lands within 400ms of a resize.
          if (R.lastResizeWall && performance.now() - R.lastResizeWall < 400) R.resizeFlickers++;
          else R.otherBlackEvents++;
        }
        lastBlack = isBlack;
        if (R.samples.length < 800) R.samples.push({ t: +t.toFixed(2), luma: +b.toFixed(1) });
      }
      post(false);
    }, 33);

    setTimeout(function () {
      clearInterval(resizeTimer); clearInterval(sampler);
      R.playedSeconds = R.maxCurrentTime;
      // In resize mode: reproduced iff most resizes produced a flicker.
      // In control mode: this should be ~0 (no resizes), the baseline.
      R.reproduced = mode === 'resize' && R.resizeFlickers >= 2;
      post(true);
    }, maxMs);
  }
})();
