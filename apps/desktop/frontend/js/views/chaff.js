// Chafftafarian: views/chaff.js
// EXIT 03 · CHAFF. Configure a run, preflight it, generate, watch it fill.
// Payload shapes: BRIDGE.md (chaffPreflight, chaffStart, chaff/chaff.log
// channels, chaff.summary report).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, kv, card, btn, pageHeader, sectionHead, emptyState, raw, esc, micro,
} from '../components/ui.js';
import { meter, logStream } from '../components/progress.js';
import * as edu from '../content.js';

const PROFILES = ['realistic-desktop', 'office-workstation', 'personal-computer',
                  'developer-workstation', 'balanced', 'storage-test', 'mixed'];
const LAYOUTS = ['realistic', 'simple', 'flat'];
const COMPLETIONS = ['keep', 'delete', 'trash'];

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

// -- tiny form DSL ------------------------------------------------------------

function field(label, inner, hint = '') {
  return `<div class="ffield"><label>${esc(label)}</label>${inner}` +
    (hint ? `<span class="fhint">${esc(hint)}</span>` : '') + `</div>`;
}
const textInput = (id, value, placeholder = '') =>
  `<input class="finput" id="${id}" type="text" value="${esc(value)}" placeholder="${esc(placeholder)}" spellcheck="false">`;
const select = (id, options, current) =>
  `<select class="finput" id="${id}">${options.map((o) =>
    `<option value="${esc(o)}"${o === current ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>`;

export async function render(root) {
  const subs = [];
  const boot = state.bootstrap || {};
  const form = {
    target: boot.app_settings?.default_target || '',
    mode: 'exact', amount: '10 GiB', percent: '50', reserve: '2 GB',
    profile: 'realistic-desktop', layout: 'realistic', seed: '0',
    completion: 'keep', types: '',
  };
  let preflight = null;
  let summary = null;

  root.innerHTML = `
    ${pageHeader({ exit: '03', title: 'Chaff', lede: edu.copy.chaffLede,
                   coords: 'SYNTHETIC · DETERMINISTIC' })}
    <div class="card-grid">
      <div id="ch-form"></div>
      <div id="ch-preflight"></div>
      <div id="ch-edu"></div>
    </div>
    <section class="section" id="ch-run-section" hidden>
      <div class="section-head">
        <span class="section-num" aria-hidden="true">R</span>
        <h2>Run in progress</h2>
        <span class="micro" id="ch-run-meta">·</span>
        <span class="grow"></span>
        <span id="ch-controls"></span>
      </div>
      <div id="ch-progress"></div>
      <div class="runstats" id="ch-stats" style="margin-top:0.8rem"></div>
      <div id="ch-log" style="margin-top:0.8rem"></div>
    </section>
    <section class="section" id="ch-summary-section" hidden>
      <div class="section-head">
        <span class="section-num" aria-hidden="true">S</span>
        <h2>Run complete</h2>
      </div>
      <div id="ch-summary"></div>
    </section>
  `;

  const $ = (sel) => root.querySelector(sel);
  const formEl = $('#ch-form'), preflightEl = $('#ch-preflight'), eduEl = $('#ch-edu');
  const stream = logStream($('#ch-log'));

  // -- form ----------------------------------------------------------------

  function renderForm() {
    formEl.innerHTML = card({
      num: '01', title: 'Job', eyebrow: 'WHAT TO GENERATE AND WHERE',
      children: `<div class="fgrid">
        ${field('Target directory',
          `<div style="display:flex;gap:0.5rem">
             ${textInput('ch-target', form.target, '/srv/chaff')}
             ${btn({ label: 'Browse', id: 'ch-pick', tip: 'Choose where runs live' })}
           </div>`,
          'Every run lands in its own Chaff_Run_<date>_<id> directory here. Existing files are never touched.')}
        ${field('Amount mode', `<div class="seg" id="ch-mode">
          ${['exact', 'percent_free', 'fill_until_reserve'].map((m) =>
            `<button type="button" data-mode="${m}" class="${m === form.mode ? 'on' : ''}">${m.replace('_', ' ')}</button>`).join('')}
        </div>`)}
        ${field('Amount', textInput('ch-amount', form.amount, '10 GiB'), 'Size units: B, KB/KiB … TB/TiB, decimal or binary.')}
        ${field('Percent of free', textInput('ch-percent', form.percent, '50'), 'Used by percent-free mode.')}
        ${field('Reserve', textInput('ch-reserve', form.reserve), 'Free space that must remain after the run. The engine re-checks between files.')}
        ${field('Profile', select('ch-profile', PROFILES, form.profile), 'Format mix, size ranges, and layout flavor.')}
        ${field('Layout', select('ch-layout', LAYOUTS, form.layout))}
        ${field('Seed', textInput('ch-seed', form.seed, '0 = random'), 'Same seed = byte-identical corpus, reproducible anywhere.')}
        ${field('Types override', textInput('ch-types', form.types, 'txt,csv,json'), 'Optional comma list; overrides the profile format mix.')}
        ${field('Completion', select('ch-completion', COMPLETIONS, form.completion), 'What happens to the run when generation completes.')}
      </div>
      <div style="margin-top:1rem;display:flex;gap:0.6rem">
        ${btn({ label: 'Preflight', id: 'ch-preflight-btn', tip: 'Check writability, free space, and estimate: writes nothing' })}
        ${btn({ label: 'Generate', kind: 'primary', id: 'ch-start', tip: 'Start the run' })}
      </div>`,
    });

    $('#ch-mode')?.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-mode]');
      if (!b) return;
      form.mode = b.dataset.mode;
      renderForm();
    });
    $('#ch-pick')?.addEventListener('click', async () => {
      const picked = await api.pickDirectory(form.target);
      if (picked) { form.target = picked; $('#ch-target').value = picked; }
    });
    const bind = (id, key) => $(id)?.addEventListener('input', (e) => { form[key] = e.target.value; });
    bind('#ch-target', 'target'); bind('#ch-amount', 'amount');
    bind('#ch-percent', 'percent'); bind('#ch-reserve', 'reserve');
    bind('#ch-seed', 'seed'); bind('#ch-types', 'types');
    $('#ch-profile')?.addEventListener('change', (e) => { form.profile = e.target.value; });
    $('#ch-layout')?.addEventListener('change', (e) => { form.layout = e.target.value; });
    $('#ch-completion')?.addEventListener('change', (e) => { form.completion = e.target.value; });
    $('#ch-preflight-btn')?.addEventListener('click', doPreflight);
    $('#ch-start')?.addEventListener('click', doStart);
  }

  function configJson() {
    const file_types = form.types.trim()
      ? Object.fromEntries(form.types.split(',').map((t) => [t.trim(), true]))
      : undefined;
    return {
      target: {
        path: form.target, mode: form.mode,
        amount: form.mode === 'exact' ? form.amount : undefined,
        percent: form.mode === 'percent_free' ? form.percent : undefined,
        reserve: form.reserve,
      },
      profile: form.profile, seed: Number(form.seed) || 0,
      directory_layout: form.layout, completion: form.completion,
      ...(file_types ? { file_types } : {}),
    };
  }

  // -- preflight -----------------------------------------------------------

  async function doPreflight() {
    preflightEl.innerHTML = card({
      num: '02', title: 'Preflight', eyebrow: 'CHECKING…',
      children: '<p class="dim">Probing the target.</p>' });
    const out = await api.chaffPreflight(configJson());
    preflight = out;
    if (out.error || out.ok === false) {
      preflightEl.innerHTML = card({
        num: '02', title: 'Preflight', eyebrow: 'REFUSED',
        children: `<p class="dim mono">${esc(out.error || 'preflight failed')}</p>` });
      return;
    }
    const rows = [
      ['TARGET', raw(`<span class="mono">${esc(out.target_path)}</span>`)],
      ['MODE', raw(`<span class="mono">${esc(out.requested_bytes != null ? fmtBytes(out.requested_bytes) : 'fill')}</span>`)],
      ['FREE NOW', raw(`<span class="mono">${fmtBytes(out.free_bytes)}</span>`)],
      ['RESERVE', raw(`<span class="mono">${fmtBytes(out.reserve_bytes)}</span>`)],
      ['EST FILES', raw(`<span class="mono">${fmtInt(out.estimated_file_count)}</span>`)],
      ['FORMATS', raw(`<span class="mono">${(out.formats || []).join(' · ') || '·'}</span>`)],
    ];
    if (out.projected_remaining_bytes != null) {
      rows.push(['REMAINING AFTER', raw(`<span class="mono">${fmtBytes(out.projected_remaining_bytes)}</span>`)]);
    }
    preflightEl.innerHTML = card({
      num: '02', title: 'Preflight', eyebrow: 'WHAT THE ENGINE SEES',
      right: chip(out.warnings?.length ? 'warn' : 'ok', out.warnings?.length ? 'NOTES' : 'CLEAR'),
      children: kv(rows) + (out.warnings || []).map((w) =>
        `<p class="micro" style="color:var(--amber)">${esc(w)}</p>`).join(''),
    });
  }

  eduEl.innerHTML = card({
    num: '03', title: 'Ground rules', eyebrow: 'READ ONCE',
    children: `
      <p><strong>${esc(edu.seedDeterminism.headline)}.</strong> ${esc(edu.seedDeterminism.body)}</p>
      <p style="margin-top:0.7rem"><strong>${esc(edu.reserveMeaning.headline)}.</strong> ${esc(edu.reserveMeaning.body)}</p>
      <p style="margin-top:0.7rem" class="dim">${esc(edu.chaffDisclaimer.headline)}: ${esc(edu.chaffDisclaimer.body)}</p>`,
  });

  // -- run panel -----------------------------------------------------------

  const runSection = $('#ch-run-section'), runMeta = $('#ch-run-meta');
  const progressEl = $('#ch-progress'), statsEl = $('#ch-stats'), controlsEl = $('#ch-controls');

  function renderControls() {
    controlsEl.innerHTML =
      btn({ label: 'Pause', id: 'ch-pause' }) +
      btn({ label: 'Resume', id: 'ch-resume' }) +
      btn({ label: 'Cancel', kind: 'danger', id: 'ch-cancel', tip: 'Stop the run; partial files are kept for inspection' });
    $('#ch-pause').onclick = () => api.chaffPause();
    $('#ch-resume').onclick = () => api.chaffResume();
    $('#ch-cancel').onclick = () => api.chaffCancel();
  }

  function renderProgress(ev) {
    const pct = ev.target_bytes ? (ev.bytes_written / ev.target_bytes) * 100 : 0;
    progressEl.innerHTML =
      `<div class="plane"><div class="plane-top"><span class="plane-label">${esc(ev.current_file || '')}</span>` +
      `<span class="micro">${fmtBytes(ev.throughput_bps)}/S</span>` +
      `<span class="plane-pct">${pct.toFixed(1)}%</span></div>` +
      `<div class="plane-track"><i style="width:${Math.min(100, pct)}%"></i></div></div>`;
    statsEl.innerHTML = `
      <div><span class="rs-num">${fmtInt(ev.files)}</span><span class="rs-label">files</span></div>
      <div><span class="rs-num">${fmtBytes(ev.bytes_written)}</span><span class="rs-label">written</span></div>
      <div><span class="rs-num">${fmtBytes(ev.target_bytes)}</span><span class="rs-label">target</span></div>
      <div><span class="rs-num">${fmtBytes(ev.free_bytes)}</span><span class="rs-label">free now</span></div>`;
  }

  async function doStart() {
    if (state.busy) return;
    if (!form.target) { doPreflight(); return; }
    summary = null;
    $('#ch-summary-section').hidden = true;
    stream.clear();
    const ack = await api.chaffStart(configJson());
    if (!ack.ok) {
      runSection.hidden = false;
      runMeta.textContent = 'REFUSED';
      stream.append(`[ERROR] ${ack.error || 'start failed'}`);
      return;
    }
    runSection.hidden = false;
    runMeta.textContent = 'RUNNING';
    renderControls();
    renderProgress({ bytes_written: 0, target_bytes: null, files: 0,
                     current_file: 'starting…', free_bytes: null, throughput_bps: 0 });
  }

  subs.push(bus.on('line', (l) => {
    if (l.channel === 'chaff') {
      try { renderProgress(JSON.parse(l.text)); } catch {}
    } else if (l.channel === 'chaff.log') {
      if (runSection.hidden) { runSection.hidden = false; renderControls(); }
      stream.append(l.text);
    }
  }));

  subs.push(bus.on('report', ({ channel, data }) => {
    if (channel !== 'chaff.summary') return;
    summary = data;
    runMeta.textContent = data.cancelled ? 'CANCELLED' : (data.ok ? 'COMPLETE' : 'FAILED');
    $('#ch-run-section').hidden = false;
    const sec = $('#ch-summary-section');
    sec.hidden = false;
    $('#ch-summary').innerHTML = card({
      num: 'S', title: data.ok ? 'Run complete' : (data.cancelled ? 'Run cancelled' : 'Run failed'),
      eyebrow: (data.run_root || '').toUpperCase(),
      right: chip(data.ok ? 'ok' : 'warn', data.status?.toUpperCase() || ''),
      children: kv([
        ['FILES', raw(`<span class="mono">${fmtInt(data.files_created)}</span>`)],
        ['WRITTEN', raw(`<span class="mono">${fmtBytes(data.bytes_written)}</span>`)],
        ['DURATION', raw(`<span class="mono">${(data.duration_s || 0).toFixed(1)}s</span>`)],
        ['THROUGHPUT', raw(`<span class="mono">${fmtBytes(data.throughput_bps)}/S</span>`)],
        ['MANIFEST', raw(`<span class="mono">${esc(data.manifest_path || 'none')}</span>`)],
      ]) + (data.error ? `<p class="micro" style="color:var(--danger)">${esc(data.error)}</p>` : '') +
        (data.warnings || []).map((w) => `<p class="micro" style="color:var(--amber)">${esc(w)}</p>`).join(''),
    });
  }));

  subs.push(bus.on('state', (s) => {
    if (!s.busy && runMeta.textContent === 'RUNNING') runMeta.textContent = 'FINISHING…';
  }));

  renderForm();
  if (boot.app_settings?.default_target) doPreflight();

  return () => subs.forEach((off) => off());
}
