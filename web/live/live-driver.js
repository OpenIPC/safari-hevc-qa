// Measure the Live page's STARTUP on Safari: from load, watch the stage media
// and count black frames AFTER the first real frame paints — i.e. a flicker on
// the way up, which is what the reporter sees on refresh (#335, Live-only).
// One measurement per load; run-live.py reloads N times and aggregates, because
// the fault is intermittent.
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var windowMs = +(q.get('ms') || 6000);

  var R = {
    ua: navigator.userAgent, startedPaint: false, firstPaintMs: null,
    blackEventsAfterPaint: 0, blackFramesAfterPaint: 0, maxLuma: 0,
    maxCurrentTime: 0, samples: [], note: '', reproduced: null, done: false
  };
  window.__progress = R;

  var probe = document.createElement('canvas');
  probe.width = 48; probe.height = 27;
  var ctx = probe.getContext('2d', { willReadFrequently: true });

  // The brightest stage media right now (the visible/promoted one carries the
  // picture; hidden idle elements are black or display:none).
  function maxLuma() {
    var els = document.querySelectorAll('.mj-stage-media');
    var best = null, ct = 0;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      try {
        if (getComputedStyle(el).display === 'none') continue;
        var w = el.videoWidth || el.naturalWidth || el.width || 0;
        if (!w) continue;
        ctx.drawImage(el, 0, 0, 48, 27);
        var d = ctx.getImageData(0, 0, 48, 27).data, s = 0;
        for (var j = 0; j < d.length; j += 4) s += 0.299 * d[j] + 0.587 * d[j + 1] + 0.114 * d[j + 2];
        var l = s / (d.length / 4);
        if (best === null || l > best) best = l;
        if (typeof el.currentTime === 'number' && el.currentTime > ct) ct = el.currentTime;
      } catch (e) {}
    }
    if (ct > R.maxCurrentTime) R.maxCurrentTime = ct;
    return best;   // null if nothing drawable yet
  }

  window.__play = function () {
    var v = document.querySelectorAll('video');
    for (var i = 0; i < v.length; i++) { try { v[i].muted = true; var p = v[i].play(); if (p && p.catch) p.catch(function () {}); } catch (e) {} }
  };
  document.addEventListener('click', window.__play, true);
  document.addEventListener('pointerdown', window.__play, true);

  function finish() {
    if (R.done) return;
    clearInterval(iv);
    // Reproduced = the picture came up and then went black again during startup.
    R.reproduced = R.startedPaint && R.blackEventsAfterPaint >= 1;
    R.done = true;
    window.__result = R;
    document.title = 'DONE';
    try {
      var body = JSON.stringify(R);
      if (navigator.sendBeacon) navigator.sendBeacon('/result', body);
    } catch (e) {}
  }

  var t0 = performance.now();
  var lastBlack = false;
  var iv = setInterval(function () {
    var l = maxLuma();
    if (l === null) return;
    if (l > R.maxLuma) R.maxLuma = l;
    var tms = Math.round(performance.now() - t0);
    if (!R.startedPaint && l > 30) { R.startedPaint = true; R.firstPaintMs = tms; }
    if (R.samples.length < 400) R.samples.push({ t: tms, luma: +l.toFixed(1) });
    if (R.startedPaint) {
      var isBlack = l < 30;
      if (isBlack) R.blackFramesAfterPaint++;
      if (isBlack && !lastBlack) { R.blackEventsAfterPaint++; R.note += 'black@' + tms + 'ms '; }
      lastBlack = isBlack;
    }
  }, 50);
  setTimeout(finish, windowMs);
})();
