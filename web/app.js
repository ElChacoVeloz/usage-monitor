/* AI Usage Monitor — pintado del tablero. Sin librerías (Chromium 106 / Android 6). */
(function () {
  'use strict';

  var DESIGN_W = 1920, DESIGN_H = 1200;
  var CIRC = 2 * Math.PI * 72;      // r=72 en un viewBox de 176
  var stage = document.getElementById('stage');
  var grid = document.getElementById('grid');
  var clockEl = document.getElementById('clock');
  var dateEl = document.getElementById('dateLine');
  var updatedEl = document.getElementById('updated');
  var totalsEl = document.getElementById('totals');
  var legendEl = document.getElementById('legend');

  var state = { version: null, generated: 0, locale: 'es-MX', data: null };

  // ------------------------------------------------------------------ escala
  function fit() {
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var s = Math.min(vw / DESIGN_W, vh / DESIGN_H);
    stage.style.transform =
      'translate(' + ((vw - DESIGN_W * s) / 2).toFixed(2) + 'px,' +
      ((vh - DESIGN_H * s) / 2).toFixed(2) + 'px) scale(' + s.toFixed(4) + ')';
  }
  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', function () {
    setTimeout(fit, 220);
  });

  // ------------------------------------------------------------------ relojes
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function paintClock() {
    var d = new Date();
    clockEl.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (window.Intl && Intl.DateTimeFormat) {
      try {
        var f = new Intl.DateTimeFormat(state.locale,
          { weekday: 'long', day: 'numeric', month: 'long' });
        var txt = f.format(d);
        dateEl.textContent = txt.charAt(0).toUpperCase() + txt.slice(1);
      } catch (e) { /* sin Intl se queda la fecha del payload */ }
    }
  }

  function humanGap(sec) {
    sec = Math.max(0, Math.round(sec));
    var d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
    var m = Math.floor((sec % 3600) / 60);
    if (d > 0) return d + ' d ' + h + ' h';
    if (h > 0) return h + ' h ' + pad(m) + ' m';
    if (m > 0) return m + ' m';
    return sec + ' s';
  }

  function paintResets() {
    var now = Date.now() / 1000;
    var nodes = document.querySelectorAll('.slot-line[data-reset]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var at = parseFloat(el.getAttribute('data-reset'));
      if (!at) continue;
      var left = at - now;
      if (left <= 0) {
        el.textContent = (el.getAttribute('data-noun') || 'reinicia') + ' ya';
        el.classList.remove('soon');
      } else {
        el.textContent = (el.getAttribute('data-noun') || 'reinicia') +
          ' en ' + humanGap(left);
        if (left < 1800) el.classList.add('soon'); else el.classList.remove('soon');
      }
    }
    if (state.generated) {
      var age = now - state.generated;
      updatedEl.textContent = 'datos de hace ' + humanGap(age).replace(' ', ' ');
    }
  }

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function level(pct) {
    if (pct >= 90) return 'var(--bad)';
    if (pct >= 70) return 'var(--warn)';
    return 'var(--accent)';
  }

  // ------------------------------------------------------------------ tarjetas
  function gaugeSvg(stat) {
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 176 176');
    var track = document.createElementNS(NS, 'circle');
    track.setAttribute('class', stat ? 'track track-dash' : 'track');
    track.setAttribute('cx', '88'); track.setAttribute('cy', '88');
    track.setAttribute('r', '72');
    svg.appendChild(track);
    if (!stat) {
      var arc = document.createElementNS(NS, 'circle');
      arc.setAttribute('class', 'arc');
      arc.setAttribute('cx', '88'); arc.setAttribute('cy', '88');
      arc.setAttribute('r', '72');
      svg.appendChild(arc);
      svg._arc = arc;
    }
    return svg;
  }

  function slotNode(w) {
    var slot = el('div', 'slot');
    slot.appendChild(el('div', 'slot-label', w.label));

    var gauge = el('div', 'gauge' + (w.kind === 'quota' ? '' : ' stat'));
    var svg = gaugeSvg(w.kind !== 'quota');
    gauge.appendChild(svg);
    var center = el('div', 'center');

    if (w.kind === 'quota') {
      var pct = Math.max(0, Math.round(w.pct || 0));
      // el % cuelga fuera del flujo: así el numeral queda centrado de verdad
      var val = el('span', 'val');
      val.appendChild(el('span', 'num', String(pct)));
      val.appendChild(el('span', 'unit', '%'));
      center.appendChild(val);
      gauge.appendChild(center);
      slot.appendChild(gauge);

      var arc = svg._arc;
      var frac = Math.min(100, Math.max(0, w.pct || 0)) / 100;
      // va por style, no por atributo: la regla .gauge .arc del CSS gana al atributo
      arc.style.strokeDashoffset = String(CIRC * (1 - frac));
      arc.style.stroke = level(w.pct || 0);
      // con 0% el remate redondo del trazo deja un punto verde que parece un bug
      arc.style.opacity = frac > 0.002 ? '1' : '0';
      var line = el('div', 'slot-line reset');
      if (w.reset_at) {
        line.setAttribute('data-reset', String(w.reset_at));
        line.setAttribute('data-noun', w.verb || 'reinicia');
        line.textContent = (w.verb || 'reinicia') + ' en …';
      } else {
        line.textContent = (w.pct || 0) < 0.05
          ? 'sin uso en esta ventana' : 'sin hora de reinicio';
        line.classList.remove('reset');
      }
      slot.appendChild(line);
      if (w.limit_text) slot.appendChild(el('div', 'slot-note', w.limit_text));
      if (w.sub) slot.appendChild(el('div', 'slot-note', w.sub));
    } else {
      center.appendChild(el('span', 'num', w.tokens_text || '0'));
      gauge.appendChild(center);
      slot.appendChild(gauge);
      slot.appendChild(el('div', 'slot-line',
        (w.calls || 0) + ' llamada' + (w.calls === 1 ? '' : 's')));
      slot.appendChild(el('div', 'slot-note', 'tokens entrada + salida'));
    }
    return slot;
  }

  function cardNode(p) {
    var card = el('article', 'card');
    card.style.setProperty('--accent', p.accent || '#a78bfa');

    var head = el('div', 'card-head');
    head.appendChild(el('div', 'card-name', p.name || '—'));
    if (p.plan) head.appendChild(el('span', 'plan', p.plan));
    var badge = el('span', 'badge ' + (p.source_mode || 'measured'), p.source_label || '');
    head.appendChild(badge);
    card.appendChild(head);

    var slots = el('div', 'slots');
    (p.windows || []).forEach(function (w) { slots.appendChild(slotNode(w)); });
    if (!p.windows || !p.windows.length) {
      var box = el('div', 'empty');
      box.appendChild(el('div', 'big', 'Sin datos todavía'));
      box.appendChild(el('div', 'hint', p.hint || p.source_note || ''));
      card.appendChild(box);
    } else {
      card.appendChild(slots);
    }

    var foot = el('div', 'card-foot');
    var left = el('div');
    if (p.footer && p.footer.model) {
      left.appendChild(el('b', null, p.footer.model));
    }
    foot.appendChild(left);
    foot.appendChild(el('div', null, (p.source_note || (p.footer && p.footer.note) || '')));
    card.appendChild(foot);
    return card;
  }

  function renderLegend(providers) {
    var seen = {}, items = [
      { k: 'live', t: 'en vivo' }, { k: 'measured', t: 'medido aquí' },
      { k: 'manual', t: 'manual' }, { k: 'error', t: 'sin lectura' }
    ];
    providers.forEach(function (p) { seen[p.source_mode] = true; });
    legendEl.innerHTML = '';
    items.forEach(function (it) {
      if (!seen[it.k]) return;
      var i = el('i', it.k, it.t);
      legendEl.appendChild(i);
    });
  }

  function render(data) {
    if (!data || !data.ok) return;
    state.data = data;
    state.generated = data.generated_at || 0;
    state.locale = data.locale || 'es-MX';
    if (data.title) document.getElementById('title').textContent = data.title;
    if (data.now && data.now.date) {
      dateEl.textContent = data.now.date;
    }

    grid.innerHTML = '';
    var provs = data.providers || [];
    grid.setAttribute('data-n', String(provs.length));
    provs.forEach(function (p) { grid.appendChild(cardNode(p)); });

    var t = data.totals || {};
    totalsEl.textContent =
      'MEDIDO EN HERMES · HOY ' + ((t.today || {}).tokens_text || '0') +
      ' tok · SEMANA ' + ((t.week || {}).tokens_text || '0') +
      ' · MES ' + ((t.month || {}).tokens_text || '0');

    renderLegend(data.providers || []);
    paintClock();
    paintResets();
    document.documentElement.classList.remove('booting');
  }

  // -------------------------------------------------------------------- carga
  function load() {
    var req = new XMLHttpRequest();
    req.open('GET', '/api/data?t=' + Date.now(), true);
    req.timeout = 25000;
    req.onreadystatechange = function () {
      if (req.readyState !== 4) return;
      if (req.status !== 200) {
        updatedEl.textContent = 'sin conexión con el servidor';
        return;
      }
      var data;
      try { data = JSON.parse(req.responseText); } catch (e) { return; }
      if (state.version && data.version && data.version !== state.version) {
        location.reload();
        return;
      }
      if (!state.version) state.version = data.version;
      render(data);
    };
    req.send();
  }

  fit();
  paintClock();
  load();
  setInterval(paintClock, 1000);
  setInterval(paintResets, 1000);
  setInterval(load, 60000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { fit(); load(); }
  });
})();
