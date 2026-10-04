// Chafftafarian: views/dispatch.js
// EXIT 06 · DISPATCH. Every departure is logged: wipe archives in the state
// dir, chaff journals in run roots. Tails run live.
// Payload shapes: BRIDGE.md (logsList / logRead / tailStart / tailStop).

import { api } from '../api.js';
import { bus } from '../shell.js';
import {
  chip, btn, pageHeader, emptyState, esc,
} from '../components/ui.js';
import { logStream } from '../components/progress.js';

const fmtBytes = (n) => {
  if (n == null) return '·';
  let f = Number(n);
  for (const u of ['B', 'KiB', 'MiB', 'GiB']) {
    if (f < 1024 || u === 'GiB') return u === 'B' ? `${f}${u}` : `${f.toFixed(1)}${u}`;
    f /= 1024;
  }
  return `${n}B`;
};

export async function render(root) {
  const subs = [];
  let logs = [];
  let tailed = null;

  root.innerHTML = `
    ${pageHeader({ exit: '06', title: 'Dispatch', lede: 'Wipe logs and chaff journals. Open one to read it or tail it live.',
                   coords: 'ARCHIVES · TAILS' })}
    <div class="dispatch-grid" id="dp-grid">
      <div id="dp-list"></div>
      <div id="dp-reader"></div>
    </div>
  `;

  const $ = (sel) => root.querySelector(sel);

  function renderList() {
    if (!logs.length) {
      $('#dp-list').innerHTML = emptyState({
        code: '06', headline: 'Nothing on file',
        guidance: 'WIPE LOGS LAND IN ~/.LOCAL/STATE/CHAFFTAFARIAN. CHAFF JOURNALS LIVE IN EACH RUN.',
      });
      return;
    }
    $('#dp-list').innerHTML = logs.map((l) => `
      <div class="logrow" data-log="${esc(l.path)}">
        <div class="lr-line">
          <span class="chip ${l.engine === 'wipe' ? 'warn' : 'ok'}"><span class="dot"></span>${l.engine.toUpperCase()}</span>
          <span class="mono" style="font-size:11px">${esc(l.name)}</span>
        </div>
        <div class="lr-meta">${fmtBytes(l.size_bytes)} · ${esc((l.mtime || '').replace('T', ' ').replace('+00:00', ''))}</div>
      </div>`).join('');
    root.querySelectorAll('[data-log]').forEach((el) => {
      el.addEventListener('click', () => select(el.dataset.log, el));
    });
  }

  async function select(path, el) {
    root.querySelectorAll('.logrow').forEach((r) => r.classList.remove('active'));
    el?.classList.add('active');
    if (tailed) {
      await api.tailStop();
      tailed = null;
    }
    const out = await api.logRead(path);
    renderReader(path, out.error ? `[ERROR] ${out.error}` : (out.text || '(empty)'));
  }

  function renderReader(path, text, live = false) {
    $('#dp-reader').innerHTML = `
      <div class="section-head">
        <span class="section-num" aria-hidden="true">L</span>
        <h2 style="font-size:1rem">${esc(path.split('/').pop() || path)}</h2>
        <span class="micro">${live ? 'LIVE TAIL' : 'ARCHIVE'}</span>
        <span class="grow"></span>
        ${btn({ label: live ? 'Stop tail' : 'Tail live', id: 'dp-tail' })}
        ${btn({ label: 'Reveal file', id: 'dp-reveal' })}
      </div>
      <div id="dp-log"></div>`;
    const stream = logStream($('#dp-log'));
    stream.set(String(text).split('\n'));
    $('#dp-tail')?.addEventListener('click', async () => {
      if (live) {
        await api.tailStop();
        tailed = null;
        renderReader(path, '');
        return;
      }
      tailed = path;
      await api.tailStart(path);
      renderReader(path, '', true);
    });
    $('#dp-reveal')?.addEventListener('click', () => api.revealFile(path));
  }

  subs.push(bus.on('line', (l) => {
    if (l.channel !== 'tail' || !tailed) return;
    const box = root.querySelector('#dp-log .logbox');
    if (box) box.innerHTML += `<span class="ln raw">${esc(l.text)}</span>`;
  }));

  logs = await api.logsList();
  logs = Array.isArray(logs) ? logs : [];
  renderList();
  renderReader('', '[INFO] Select a log on the left.\n[INFO] Wipe logs are in the state dir. Chaff journals are in each run.');

  return () => {
    api.tailStop().catch(() => {});
    subs.forEach((off) => off());
  };
}
