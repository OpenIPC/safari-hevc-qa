// Measure the Live page's STARTUP on Safari: from load, watch the stage media
// and count black frames AFTER the first real frame paints — i.e. a flicker on
// the way up, which is what the reporter sees on refresh (#335, Live-only).
// One measurement per load; run-live.py reloads N times and aggregates, because
// the fault is intermittent.
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var windowMs = +(q.get('ms') || 6000);
  // Simulate the real page's layout settling: change the stage size once, some
  // ms after load, the way the viewport/navbar does (a URL-bar collapse grows
  // the viewport ~1s in). preview-zoom's ResizeObserver then re-lays-out the
  // picture — the #294-class reflow. Off unless ?resizeAt is given.
  var resizeAt = +(q.get('resizeAt') || 0);
  var resizeTo = (q.get('resizeTo') || '900x620').split('x');
  if (resizeAt > 0) {
    setTimeout(function () {
      var st = document.getElementById('mj-stage');
      if (st) { st.style.width = (+resizeTo[0]) + 'px'; st.style.height = (+resizeTo[1]) + 'px'; }
    }, resizeAt);
  }

  var R = {
    ua: navigator.userAgent, startedPaint: false, firstPaintMs: null,
    blackEventsAfterPaint: 0, blackFramesAfterPaint: 0, maxLuma: 0,
    maxCurrentTime: 0, intrinsic: null, aspect: null,
    sizeChangesAfterPaint: 0, rectSeq: [], firstRect: null, lastRect: null,
    samples: [], note: '', reproduced: null, done: false
  };
  window.__progress = R;

  // The on-screen box of the visible picture — a LATE change to it is a layout
  // reflow (the #294 class: the picture jumps size a second after load), which
  // reads as a flicker without any black frame. Tracked separately from luma.
  function visibleRect() {
    var els = document.querySelectorAll('.mj-stage-media');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      try {
        if (getComputedStyle(el).display === 'none') continue;
        var w = el.videoWidth || el.naturalWidth || el.width || 0;
        if (!w) continue;
        var r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (el.videoWidth) { R.intrinsic = el.videoWidth + 'x' + el.videoHeight; R.aspect = +(el.videoWidth / el.videoHeight).toFixed(3); }
        return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), y: Math.round(r.top) };
      } catch (e) {}
    }
    return null;
  }

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
    // Reproduced = after the picture came up, it either went black again OR its
    // on-screen size changed (a layout reflow — the #294-class flicker).
    R.reproduced = R.startedPaint && (R.blackEventsAfterPaint >= 1 || R.sizeChangesAfterPaint >= 1);
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
    var tms = Math.round(performance.now() - t0);
    var rect = visibleRect();
    var l = maxLuma();

    // Track the picture's on-screen size and flag a change after the first paint.
    if (rect) {
      if (!R.firstRect) R.firstRect = rect;
      if (R.lastRect && (Math.abs(rect.w - R.lastRect.w) > 1 || Math.abs(rect.h - R.lastRect.h) > 1)) {
        if (R.rectSeq.length < 40) R.rectSeq.push({ t: tms, w: rect.w, h: rect.h, x: rect.x, y: rect.y });
        if (R.startedPaint) { R.sizeChangesAfterPaint++; R.note += 'resize@' + tms + 'ms->' + rect.w + 'x' + rect.h + ' '; }
      }
      R.lastRect = rect;
    }

    if (l === null) return;
    if (l > R.maxLuma) R.maxLuma = l;
    if (!R.startedPaint && l > 30) {
      R.startedPaint = true; R.firstPaintMs = tms;
      if (rect) R.rectSeq.push({ t: tms, w: rect.w, h: rect.h, x: rect.x, y: rect.y, paint: true });
    }
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
