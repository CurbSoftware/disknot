// Chafftafarian: shell.js
// The app chassis: a tiny pub-sub bus, shared core state, and the status
// bar. Views never touch the bridge for state: they read `state` and
// subscribe to bus events:
//
//   bus.on('state',   fn)  core state changed (mirrored in `state`)
//   bus.on('line',    fn)  {channel, text}: every lineReady, all channels
//   bus.on('report',  fn)  {channel, data}: every reportReady
//   bus.on('escape',  fn)  Esc pressed at shell level
//
// bus.on returns an unsubscribe; views call theirs from their cleanup.
// Payload shapes: BRIDGE.md.

const handlers = new Map();

export const bus = {
  on(evt, fn) {
    if (!handlers.has(evt)) handlers.set(evt, []);
    handlers.get(evt).push(fn);
    return () => {
      const arr = handlers.get(evt);
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    };
  },
  emit(evt, payload) {
    for (const fn of [...(handlers.get(evt) || [])]) fn(payload);
  },
};

// Shared, read-mostly snapshot. Mutated only here; announced via bus 'state'.
export const state = {
  ready: false,        // bootstrap arrived
  coreReady: false,    // core resolved (always true today; grows with probes)
  coreVersion: null,
  busy: false,
  kind: null,          // 'chaff' | 'wipe' | null
  started: null,
  lastProbe: null,     // last device/tool probe report
  targetFreeGb: null,  // free space on the default chaff target
  toolsMissing: [],
  bootstrap: null,
};

const $ = (id) => document.getElementById(id);

function renderSystemsChip() {
  const el = $('sb-systems');
  if (!el) return;
  if (!state.coreReady) {
    el.innerHTML = '<span class="chip err"><span class="dot"></span>SYSTEMS OFFLINE</span>';
  } else if (state.lastProbe) {
    el.innerHTML = state.lastProbe.ok
      ? '<span class="chip ok"><span class="dot"></span>SYSTEMS ONLINE</span>'
      : '<span class="chip err"><span class="dot"></span>PROBLEMS FOUND</span>';
  } else if (state.toolsMissing.length) {
    el.innerHTML = '<span class="chip err"><span class="dot"></span>TOOLS MISSING</span>';
  } else {
    el.innerHTML = '<span class="chip idle"><span class="dot"></span>SYSTEMS UNCHECKED</span>';
  }
}

function renderRunChip() {
  const el = $('sb-run');
  if (!el) return;
  el.hidden = !state.busy;
  if (state.busy) {
    const label = state.kind === 'wipe' ? 'WIPE IN PROGRESS'
      : state.kind === 'chaff' ? 'CHAFF RUNNING'
      : 'WORKING';
    el.innerHTML = `<span class="chip warn"><span class="dot"></span>${label}</span>`;
  }
}

function renderCore() {
  const el = $('sb-engine');
  if (!el) return;
  const v = state.coreVersion;
  el.textContent = v && v !== 'unknown' ? `CORE v${v}` : 'CORE';
}

function renderDisk() {
  const el = $('sb-disk');
  if (!el) return;
  el.textContent = state.targetFreeGb != null
    ? `TARGET · ${state.targetFreeGb.toFixed(0)} GB FREE` : '';
}

function startClock() {
  const el = $('sb-clock');
  if (!el) return;
  const tick = () => {
    const d = new Date();
    el.textContent = [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map((n) => String(n).padStart(2, '0')).join(':');
  };
  tick();
  setInterval(tick, 1000);
}

function applyBootstrap(b) {
  state.bootstrap = b;
  state.ready = true;
  state.coreReady = !!(b.core && b.core.ready);
  if (b.core && b.core.version) state.coreVersion = b.core.version;
  state.busy = !!b.busy;
  state.kind = b.kind ?? null;
  const tools = b.tools || {};
  state.toolsMissing = Object.entries(tools)
    .filter(([, p]) => !p).map(([n]) => n);
  const disk = b.disk && b.disk.target;
  if (disk && typeof disk.free_gb === 'number') state.targetFreeGb = disk.free_gb;
}

function renderStatusbar() {
  renderSystemsChip();
  renderRunChip();
  renderCore();
  renderDisk();
}

export async function initShell(api) {
  startClock();

  // Tooltips pick a side at show time so the bubble never grows past the
  // view's right edge (that would open a horizontal scrollbar). Pure-CSS
  // display; this only flips the anchor class.
  const flipTip = (el) => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    const viewR = document.getElementById('view').getBoundingClientRect();
    el.classList.toggle('tip-end', r.right - viewR.left + 284 > viewR.width);
  };
  document.addEventListener('pointerover', (e) => {
    flipTip(e.target.closest?.('[data-tip]'));
  });
  document.addEventListener('focusin', (e) => {
    flipTip(e.target.closest?.('[data-tip]'));
  });

  api.on('stateChanged', (s) => {
    state.busy = !!s.busy;
    state.kind = s.kind ?? null;
    state.started = s.started ?? null;
    if (s.core_version && s.core_version !== 'unknown') {
      state.coreVersion = s.core_version;
    }
    renderRunChip();
    renderCore();
    bus.emit('state', { ...state });
  });

  api.on('lineReady', (l) => bus.emit('line', l));

  api.on('reportReady', (r) => {
    if (r.channel === 'probe' && r.data && !r.data.error) state.lastProbe = r.data;
    renderSystemsChip();
    bus.emit('report', r);
    bus.emit(`report:${r.channel}`, r.data);
  });

  try {
    const b = await api.getBootstrap();
    applyBootstrap(b || {});
  } catch (err) {
    console.error('bootstrap failed', err);
    state.coreReady = false;
  }
  renderStatusbar();
  bus.emit('state', { ...state });
}
