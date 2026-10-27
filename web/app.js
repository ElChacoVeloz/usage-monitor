/* AI Usage Monitor — tema LCARS (propuesta). Mismos datos que el tablero
   original (/api/data); sin librerías, ES5 para WebView/iPad viejos. */
(function () {
  'use strict';

  var DESIGN_W = 1920, DESIGN_H = 1200;
  var stage = document.getElementById('stage');
  var grid = document.getElementById('grid');
  var side = document.getElementById('side');
  var chips = document.getElementById('chips');
  var clockEl = document.getElementById('clock');
  var dateEl = document.getElementById('dateLine');
  var updatedEl = document.getElementById('updated');
  var totalsEl = document.getElementById('totals');
  var legendEl = document.getElementById('legend');
  var botCode = document.getElementById('botCode');

  var state = { version: null, generated: 0, locale: 'es-MX', data: null };

  // Paleta LCARS por proveedor (el acento del config es violeta/verde/naranja;
  // aquí se traduce a la paleta del panel). Desconocidos rotan por la lista.
  var LCARS = { opencode: '#2ca9df', chatgpt: '#ffb98e', claude: '#ff8a66' };
  var ROTA = ['#2ca9df', '#ffb98e', '#ff8a66', '#c4c8d8'];

  // ------------------------------------------------------------------ escala
  function fit() {
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    if (!vw || !vh) return;
    var stageH = Math.round(DESIGN_W * (vh / vw));
    stageH = Math.max(DESIGN_H, Math.min(1700, stageH));
    document.documentElement.style.setProperty('--stage-w', DESIGN_W + 'px');
    document.documentElement.style.setProperty('--stage-h', stageH + 'px');
    var s = Math.min(vw / DESIGN_W, vh / stageH);
    stage.style.transform =
      'translate(' + ((vw - DESIGN_W * s) / 2).toFixed(2) + 'px,' +
      ((vh - stageH * s) / 2).toFixed(2) + 'px) scale(' + s.toFixed(4) + ')';
  }
  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', function () { setTimeout(fit, 220); });

  // --------------------------------------------------------------- no dormirse
  function keepAwake() {
    if (!navigator.wakeLock || !navigator.wakeLock.request) return;
    try { navigator.wakeLock.request('screen').catch(function () {}); } catch (e) {}
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) keepAwake();
  });
  document.addEventListener('pointerdown', keepAwake, { once: true });
  keepAwake();

  // ------------------------------------------------------------------ relojes
  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function paintClock() {
    var d = new Date();
    clockEl.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (window.Intl && Intl.DateTimeFormat) {
      try {
        var f = new Intl.DateTimeFormat(state.locale,
          { weekday: 'long', day: 'numeric', month: 'long' });
        dateEl.textContent = f.format(d) + ' · ' + d.getFullYear();
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
    var nodes = document.querySelectorAll('.w-reset[data-reset]');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var at = parseFloat(n.getAttribute('data-reset'));
      if (!at) continue;
      var left = at - now;
      var noun = n.getAttribute('data-noun') || 'reinicia';
      if (left <= 0) {
        n.textContent = noun + ' ya';
        n.classList.remove('soon');
      } else {
        n.textContent = noun + ' en ' + humanGap(left);
        if (left < 1800) n.classList.add('soon'); else n.classList.remove('soon');
      }
    }
    if (state.generated) {
      updatedEl.textContent = 'datos de hace ' + humanGap(now - state.generated);
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
    if (pct >= 90) return 'var(--org)';
    if (pct >= 70) return 'var(--amb)';
    return 'var(--accent)';
  }

  // códigos de panel: salen de los datos (posición y %), no son decoración al azar
  function code(i, j, v) {
    return pad(i + 1) + '-' + pad(j + 1) + (v != null ? ' · ' + v : '');
  }

  // ------------------------------------------------------------------ ventana
  function winNode(w, i, j) {
    var quota = w.kind === 'quota';
    var pct = Math.max(0, w.pct || 0);
    var box = el('div', 'win' + (quota ? '' : ' stat') + (quota && pct >= 90 ? ' bad' : ''));

    var lab = el('div', 'w-label');
    lab.appendChild(el('span', 'tag', w.label || '—'));
    lab.appendChild(el('span', null, quota ? 'cuota' : 'medido'));
    lab.appendChild(el('span', 'code', code(i, j, quota ? Math.round(pct * 10) : w.calls || 0)));
    box.appendChild(lab);

    var main = el('div', 'w-main');
    var num = el('div', 'w-num');
    var sideTxt = el('div', 'w-side');

    if (quota) {
      num.appendChild(document.createTextNode(String(Math.round(pct))));
      num.appendChild(el('span', 'u', '%'));
      num.style.color = level(pct);
      var r = el('div', 'w-reset');
      if (w.reset_at) {
        r.setAttribute('data-reset', String(w.reset_at));
        r.setAttribute('data-noun', w.verb || 'reinicia');
        r.textContent = (w.verb || 'reinicia') + ' en …';
      } else {
        r.textContent = pct < 0.05 ? 'sin uso en esta ventana' : 'sin hora de reinicio';
      }
      sideTxt.appendChild(r);
      if (w.limit_text) sideTxt.appendChild(el('div', 'w-note', w.limit_text));
      if (w.sub) sideTxt.appendChild(el('div', 'w-note', w.sub));
    } else {
      num.textContent = w.tokens_text || '0';
      sideTxt.appendChild(el('div', 'w-reset',
        (w.calls || 0) + ' llamada' + (w.calls === 1 ? '' : 's')));
      sideTxt.appendChild(el('div', 'w-note', 'tokens entrada + salida'));
      if (w.sub) sideTxt.appendChild(el('div', 'w-note', w.sub));
    }
    main.appendChild(num);
    main.appendChild(sideTxt);
    box.appendChild(main);

    var meter = el('div', 'meter' + (quota ? '' : ' dash'));
    if (quota) {
      var f = el('div', 'fill');
      f.style.width = Math.min(100, pct).toFixed(1) + '%';
      f.style.background = level(pct);
      // con 0% no se pinta nada: un segmento suelto parecería consumo
      if (pct < 0.05) f.style.width = '0';
      meter.appendChild(f);
    }
    box.appendChild(meter);
    if (quota) {
      var t = el('div', 'ticks');
      ['0', '25', '50', '75', '100'].forEach(function (x) { t.appendChild(el('span', null, x)); });
      box.appendChild(t);
    }
    return box;
  }

  // ------------------------------------------------------------------- panel
  function panelNode(p, i) {
    var panel = el('article', 'panel');
    var acc = LCARS[p.id] || ROTA[i % ROTA.length];
    panel.style.setProperty('--accent', acc);

    var head = el('div', 'p-head');
    head.appendChild(el('span', 'p-cap'));
    head.appendChild(el('span', 'p-name', p.name || '—'));
    if (p.plan) head.appendChild(el('span', 'p-plan', p.plan));
    head.appendChild(el('span', 'p-fill'));
    head.appendChild(el('span', 'p-badge ' + (p.source_mode || 'measured'), p.source_label || ''));
    head.appendChild(el('span', 'p-end'));
    panel.appendChild(head);

    var body = el('div', 'p-body');
    var ws = p.windows || [];
    if (!ws.length) {
      var box = el('div', 'empty');
      box.appendChild(el('div', 'big', p.source_label || 'Sin datos todavía'));
      box.appendChild(el('div', 'hint', p.hint || p.source_note || ''));
      body.appendChild(box);
    } else {
      ws.forEach(function (w, j) { body.appendChild(winNode(w, i, j)); });
    }
    panel.appendChild(body);

    var foot = el('div', 'p-foot');
    foot.appendChild(el('span', 'fc'));
    var model = p.footer && p.footer.model;
    foot.appendChild(el('span', 'fm', model || ('nodo ' + pad(i + 1))));
    foot.appendChild(el('span', 'ff'));
    foot.appendChild(el('span', 'fn', p.source_note || (p.footer && p.footer.note) || ''));
    panel.appendChild(foot);
    return panel;
  }

  // ----------------------------------------------------------- barra lateral
  function blk(cls, k, v, h) {
    var b = el('div', 'blk ' + cls);
    if (h) b.style.height = h + 'px';
    if (k) b.appendChild(el('span', 'k', k));
    if (v != null) b.appendChild(el('span', 'v', v));
    return b;
  }

  function numBlk(color, big, small) {
    var b = el('div', 'num');
    var tab = el('span', 'tab'); tab.style.background = color;
    b.appendChild(tab);
    var n = el('div', 'n');
    n.appendChild(el('b', null, big));
    n.appendChild(el('span', null, small));
    b.appendChild(n);
    return b;
  }

  function renderSide(data) {
    side.innerHTML = '';
    var provs = data.providers || [];
    var t = data.totals || {};
    side.appendChild(blk('s-org', 'consumo', 'AI', 74));
    // un dato grande por proveedor: la ventana más corta con cuota
    provs.forEach(function (p, i) {
      var w = null;
      (p.windows || []).forEach(function (x) { if (!w && x.kind === 'quota') w = x; });
      var acc = LCARS[p.id] || ROTA[i % ROTA.length];
      if (w) {
        side.appendChild(numBlk(acc, Math.round(w.pct || 0) + '%',
          (p.id || '').toUpperCase() + ' · ' + (w.label || '')));
      } else {
        side.appendChild(numBlk(acc, '—', (p.id || '').toUpperCase() + ' · ' + (p.source_label || '')));
      }
    });
    side.appendChild(blk('s-lav', 'hoy · tokens', (t.today || {}).tokens_text || '0', 84));
    side.appendChild(blk('s-gry', 'llamadas hoy', String((t.today || {}).calls || 0), 84));
    side.appendChild(blk('s-pch', 'semana', (t.week || {}).tokens_text || '0', 84));
    side.appendChild(blk('s-lav fill', 'rev', (data.version || '').slice(0, 6)));
  }

  // ------------------------------------------------------------- pie / leyenda
  function renderBottom(data) {
    var t = data.totals || {};
    totalsEl.innerHTML = '';
    totalsEl.appendChild(el('div', 'lbl', 'medido en hermes'));
    [['hoy', t.today], ['semana', t.week], ['mes', t.month]].forEach(function (x) {
      var c = el('div', 't');
      var o = x[1] || {};
      c.appendChild(el('span', null, x[0]));
      c.appendChild(el('b', null, o.tokens_text || '0'));
      c.appendChild(el('i', null, (o.calls || 0) + ' llamadas'));
      totalsEl.appendChild(c);
    });

    var items = [
      { k: 'live', t: 'en vivo' }, { k: 'measured', t: 'medido' },
      { k: 'manual', t: 'manual' }, { k: 'error', t: 'sin lectura' },
      { k: 'empty', t: 'sin datos' }
    ];
    var count = {};
    (data.providers || []).forEach(function (p) {
      count[p.source_mode] = (count[p.source_mode] || 0) + 1;
    });
    legendEl.innerHTML = '';
    items.forEach(function (it) {
      if (!count[it.k]) return;
      var b = el('div', 'lg ' + (it.k === 'empty' ? 'measured' : it.k));
      b.appendChild(el('b', null, it.t));
      var s = el('span');
      s.appendChild(el('em', null, count[it.k] + ' / ' + (data.providers || []).length));
      b.appendChild(s);
      legendEl.appendChild(b);
    });
    botCode.textContent = 'ref ' + (data.version || '').slice(0, 8);
  }

  function renderChips(data) {
    chips.innerHTML = '';
    var provs = data.providers || [];
    var live = 0;
    provs.forEach(function (p) { if (p.source_mode === 'live') live++; });
    var c1 = el('span', 'chip s-lav', 'puerto ' + (location.port || '80')); chips.appendChild(c1);
    var c2 = el('span', 'chip s-gry', provs.length + ' proveedores'); c2.style.color = '#c4c8d8';
    chips.appendChild(c2);
    var c3 = el('span', 'chip ' + (live === provs.length ? 's-cyn' : 'o'),
      live + ' en vivo');
    chips.appendChild(c3);
  }

  function render(data) {
    if (!data || !data.ok) return;
    state.data = data;
    state.generated = data.generated_at || 0;
    state.locale = data.locale || 'es-MX';
    if (data.title) document.getElementById('title').textContent = data.title;
    if (data.now && data.now.date) dateEl.textContent = data.now.date;

    grid.innerHTML = '';
    (data.providers || []).forEach(function (p, i) { grid.appendChild(panelNode(p, i)); });
    renderSide(data);
    renderBottom(data);
    renderChips(data);
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

  window.lcarsDebug = { render: render, state: state };

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
