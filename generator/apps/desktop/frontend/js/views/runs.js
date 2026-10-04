// Chafftafarian: views/runs.js
// EXIT 04 · RUNS. Every chaff run on record: inspect, verify, clean up.
// Payload shapes: BRIDGE.md (runsList, runInspect, verifyStart / "verify"
// report, runCleanup).

import { api } from '../api.js';
import { bus } from '../shell.js';
import {
  chip, kv, card, btn, pageHeader, sectionHead, emptyState, table, raw, esc,
} from '../components/ui.js';
import * as edu from '../content.js';

const fmtBytes = (n) => {
  if (n == null) return '·';
  let f = Number(n);
  for (const u of ['B', 'KiB', 'MiB', 'GiB', 'TiB']) {
    if (f < 1024 || u === 'TiB') return u === 'B' ? `${f}${u}` : `${f.toFixed(1)}${u}`;
    f /= 1024;
  }
  return `${n}B`;
};
const fmtInt = (n) => (n == null ? '·' : Number(n).toLocaleString());

const STATUS_CHIP = {
  completed: ['ok', 'COMPLETED'], cancelled: ['warn', 'CANCELLED'],
  failed: ['err', 'FAILED'], running: ['warn', 'RUNNING'],
};

export async function render(root) {
  const subs = [];
  let runs = [];
  let selected = null;

  root.innerHTML = `
    ${pageHeader({ exit: '04', title: 'Runs', lede: edu.copy.runsLede,
                   coords: 'MANIFESTS · VERDICTS' })}
    <div id="rn-list"></div>
    <section class="section" id="rn-detail-section" hidden>
      ${sectionHead({ num: 'D', title: 'Run detail' })}
      <div id="rn-detail"></div>
      <div id="rn-verify" style="margin-top:0.9rem"></div>
    </section>
    <section class="section" id="rn-verify-section" hidden>
      ${sectionHead({ num: 'V', title: 'Verification report' })}
      <div id="rn-verify-report"></div>
    </section>
  `;

  const $ = (sel) => root.querySelector(sel);

  async function refresh() {
    const list = await api.runsList('');
    runs = Array.isArray(list) ? list : [];
    renderList();
  }

  function renderList() {
    if (!runs.length) {
      $('#rn-list').innerHTML = emptyState({
        code: '04',
        headline: 'No runs on record',
        guidance: 'GENERATE ONE FROM EXIT 03 CHAFF. RUNS APPEAR HERE WHEN THEIR MARKER FILE IS WRITTEN',
      });
      return;
    }
    $('#rn-list').innerHTML = card({
      num: '04', title: 'Run history', eyebrow: `${runs.length} ON RECORD`,
      right: btn({ label: 'Refresh', id: 'rn-refresh' }),
      children: table(
        ['Run', 'Status', 'Files', 'Written', 'Created'],
        runs.map((r) => {
          const [st, label] = STATUS_CHIP[r.status] || ['idle', (r.status || '?').toUpperCase()];
          return [
            raw(`<button class="btn ghost" data-run="${esc(r.root)}" style="padding:0.2rem 0.5rem">
                   <span class="mono">${esc(r.run_id)}</span></button>`),
            raw(chip(st, label)),
            raw(`<span class="mono">${fmtInt(r.file_count)}</span>`),
            raw(`<span class="mono">${fmtBytes(r.bytes_written)}</span>`),
            raw(`<span class="micro">${esc((r.created_at || '').replace('T', ' ').slice(0, 19))}</span>`),
          ];
        })),
    });
    $('#rn-refresh')?.addEventListener('click', refresh);
    root.querySelectorAll('[data-run]').forEach((b) => {
      b.addEventListener('click', () => selectRun(b.dataset.run));
    });
  }

  async function selectRun(path) {
    selected = path;
    const detail = await api.runInspect(path);
    const sec = $('#rn-detail-section');
    sec.hidden = false;
    const [st, label] = STATUS_CHIP[detail.status] || ['idle', (detail.status || '?').toUpperCase()];
    $('#rn-detail').innerHTML = card({
      num: 'D', title: detail.run_id || 'Run',
      eyebrow: (detail.root || '').toUpperCase(),
      right: raw(chip(st, label)),
      children: kv([
        ['CREATED', raw(`<span class="mono">${esc(detail.created_at || '·')}</span>`)],
        ['PROFILE', raw(`<span class="mono">${esc(detail.profile ?? '·')}</span>`)],
        ['SEED', raw(`<span class="mono">${esc(detail.seed ?? '·')}</span>`)],
        ['FILES', raw(`<span class="mono">${fmtInt(detail.file_count)}</span>`)],
        ['WRITTEN', raw(`<span class="mono">${fmtBytes(detail.bytes_written)}</span>`)],
        ['APP VERSION', raw(`<span class="mono">${esc(detail.app_version || '·')}</span>`)],
      ]) + (detail.error ? `<p class="micro" style="color:var(--amber)">${esc(detail.error)}</p>` : '') +
      (detail.largest?.length ? sectionHead({ num: 'L', title: 'Largest files' }) +
        table(['Path', 'Size'], detail.largest.map((f) => [
          raw(`<span class="mono">${esc(f.path)}</span>`),
          raw(`<span class="mono">${fmtBytes(f.size_bytes)}</span>`),
        ])) : ''),
    });
    $('#rn-verify').innerHTML = card({
      num: 'A', title: 'Actions', eyebrow: 'VERIFY AND CLEAN UP',
      children: `
        <div style="display:flex;gap:0.6rem;flex-wrap:wrap">
          ${btn({ label: 'Verify (full)', id: 'rn-verify-full', tip: 'This option hashes every file against the manifest.' })}
          ${btn({ label: 'Verify (sample)', id: 'rn-verify-sample', tip: 'This option hashes a deterministic sample of the manifest.' })}
          ${btn({ label: 'Open directory', id: 'rn-open' })}
          ${btn({ label: 'Delete run', kind: 'danger', id: 'rn-delete', tip: 'This option deletes this run folder. Nothing else.' })}
        </div>
        <div class="confirmbox" id="rn-confirm" hidden style="margin-top:0.8rem">
          <span class="micro">TYPE DELETE TO CONFIRM</span>
          <input class="finput" id="rn-confirm-input" maxlength="10" spellcheck="false">
          ${btn({ label: 'Really delete', kind: 'danger', id: 'rn-confirm-go' })}
        </div>`,
    });
    $('#rn-verify-full').onclick = () => startVerify('full');
    $('#rn-verify-sample').onclick = () => startVerify('sample');
    $('#rn-open').onclick = () => api.openPath(selected);
    $('#rn-delete').onclick = () => { $('#rn-confirm').hidden = false; $('#rn-confirm-input').focus(); };
    $('#rn-confirm-go').onclick = doDelete;
    $('#rn-confirm-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.value.trim().toUpperCase() === 'DELETE') doDelete();
    });
  }

  async function startVerify(mode) {
    $('#rn-verify-section').hidden = false;
    $('#rn-verify-report').innerHTML =
      card({ num: 'V', title: 'Verifying', eyebrow: `MODE ${mode.toUpperCase()}`,
             children: '<p class="dim">Hashing against the manifest…</p>' });
    const ack = await api.verifyStart(selected, mode);
    if (!ack.ok) {
      $('#rn-verify-report').innerHTML = card({
        num: 'V', title: 'Verification refused', eyebrow: '',
        children: `<p class="dim mono">${esc(ack.error)}</p>` });
    }
  }

  async function doDelete() {
    const typed = $('#rn-confirm-input')?.value?.trim()?.toUpperCase();
    if (typed !== 'DELETE') return;
    const out = await api.runCleanup(selected, 'delete');
    if (out.ok) {
      $('#rn-detail-section').hidden = true;
      $('#rn-confirm').hidden = true;
      refresh();
    } else {
      $('#rn-verify-report').hidden = false;
      $('#rn-verify-report').innerHTML = card({
        num: '!', title: 'Cleanup refused', eyebrow: 'SAFETY CHECK FAILED',
        children: `<p class="dim mono">${esc(out.error)}</p>` });
    }
  }

  subs.push(bus.on('report', ({ channel, data }) => {
    if (channel !== 'verify') return;
    $('#rn-verify-section').hidden = false;
    const counts = data.counts || {};
    const rows = Object.entries(counts).filter(([, n]) => n > 0);
    $('#rn-verify-report').innerHTML = card({
      num: 'V', title: data.ok ? 'Intact' : 'Problems found',
      eyebrow: `${(data.run_root || '').toUpperCase()} · MODE ${(data.mode || '').toUpperCase()}`,
      right: raw(chip(data.ok ? 'ok' : 'err', data.ok ? 'INTACT' : 'AFFECTED')),
      children: kv([
        ['CHECKED', raw(`<span class="mono">${fmtInt(data.files_checked)} of ${fmtInt(data.files_total)}</span>`)],
        ['BYTES', raw(`<span class="mono">${fmtBytes(data.bytes_verified)} of ${fmtBytes(data.bytes_expected)}</span>`)],
        ['VERDICTS', raw(`<span class="mono">${rows.map(([k, v]) => `${v} ${k}`).join(' · ') || '·'}</span>`)],
      ]) + (data.cancelled ? '<p class="micro" style="color:var(--amber)">cancelled before completion</p>' : '') +
      (data.affected || []).map((a) =>
        `<p class="micro mono" style="color:var(--danger)">${esc(a.verdict)} ${esc(a.relative_path)}</p>`).join(''),
    });
  }));

  subs.push(bus.on('report', ({ channel }) => {
    if (channel === 'chaff.summary') refresh();  // a new run just landed
  }));

  await refresh();
  return () => subs.forEach((off) => off());
}
