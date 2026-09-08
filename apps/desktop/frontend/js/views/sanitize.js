// Chafftafarian: views/sanitize.js
// EXIT 02 · SANITIZE. Plan it, confirm it by typing DESTROY plus the exact
// device name, watch every pass. Payload shapes: BRIDGE.md (wipePlan,
// wipeStart, wipe/wipe.log channels, wipe.summary report).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, kv, card, btn, pageHeader, sectionHead, emptyState, table, raw, esc,
} from '../components/ui.js';
import { logStream } from '../components/progress.js';
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
const fmtDur = (s) => (s == null ? 'unknown' : s >= 5400 ? `~${Math.round(s / 3600)} h` : `~${Math.round(s / 60)} min`);

const PASS_KIND_CHIP = { sanitize: 'ok', zeros: 'idle', ones: 'idle', verify: 'warn' };

export async function render(root) {
  const subs = [];
  let devices = [];
  let plan = null;
  const form = {
    device: state.pendingDevice || '', verify: false, quick: false,
    unmount: false, confirm_word: '', confirm_device: '',
  };
  state.pendingDevice = null;

  root.innerHTML = `
    ${pageHeader({ exit: '02', title: 'Sanitize', lede: edu.copy.sanitizeLede,
                   coords: 'IRREVERSIBLE · BY DESIGN' })}
    <div class="card-grid">
      <div id="sn-form"></div>
      <div id="sn-plan"></div>
    </div>
    <div id="sn-safety"></div>
    <section class="section" id="sn-run-section" hidden>
      ${sectionHead({ num: 'W', title: 'Wipe in progress', meta: '' })}
      <div id="sn-progress"></div>
      <div id="sn-log" style="margin-top:0.8rem"></div>
    </section>
  `;

  const $ = (sel) => root.querySelector(sel);
  const stream = logStream($('#sn-log'));

  function renderForm() {
    const options = ['<option value="">· choose a device ·</option>']
      .concat(devices.map((d) =>
        `<option value="${esc(d.path)}"${d.path === form.device ? ' selected' : ''}>` +
        `${esc(d.path)}: ${esc(d.model || d.kname)} (${fmtBytes(d.size_bytes)})` +
        `${d.os_disk ? ' · OS DISK' : ''}</option>`));
    $('#sn-form').innerHTML = card({
      num: '01', title: 'Target and plan', eyebrow: 'WHAT WILL BE DESTROYED',
      children: `<div class="fgrid">
        <div class="ffield"><label>Device</label>
          <select class="finput" id="sn-device">${options.join('')}</select></div>
        <div class="ffield"><label>Plan</label>
          <div class="seg" id="sn-planmode">
            <button type="button" data-quick="0" class="${form.quick ? '' : 'on'}">paranoid 6-pass</button>
            <button type="button" data-quick="1" class="${form.quick ? 'on' : ''}">quick</button>
          </div></div>
        <div class="ffield"><label>Options</label>
          <label style="display:flex;gap:0.5rem;align-items:center">
            <input type="checkbox" id="sn-verify"${form.verify ? ' checked' : ''}> verify after (sampled read-back)</label>
          <label style="display:flex;gap:0.5rem;align-items:center;margin-top:0.4rem">
            <input type="checkbox" id="sn-unmount"${form.unmount ? ' checked' : ''}> unmount partitions for me</label>
        </div>
      </div>
      <div style="margin-top:1rem">
        ${btn({ label: 'Build the plan', kind: 'primary', id: 'sn-plan-btn', tip: 'Probe capabilities and compute passes: writes nothing' })}
      </div>`,
    });
    $('#sn-device').addEventListener('change', (e) => { form.device = e.target.value; });
    $('#sn-planmode').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-quick]');
      if (!b) return;
      form.quick = b.dataset.quick === '1';
      renderForm();
    });
    $('#sn-verify').addEventListener('change', (e) => { form.verify = e.target.checked; });
    $('#sn-unmount').addEventListener('change', (e) => { form.unmount = e.target.checked; });
    $('#sn-plan-btn').addEventListener('click', buildPlan);
  }

  async function buildPlan() {
    if (!form.device) return;
    plan = await api.wipePlan({
      device: form.device, verify: form.verify, quick: form.quick,
      unmount: form.unmount,
    });
    renderPlan();
  }

  function renderPlan() {
    if (!plan) return;
    if (plan.error) {
      $('#sn-plan').innerHTML = card({
        num: '02', title: 'Plan', eyebrow: 'REFUSED',
        children: `<p class="dim mono">${esc(plan.error)}</p>` });
      $('#sn-safety').innerHTML = '';
      return;
    }
    $('#sn-plan').innerHTML = card({
      num: '02', title: 'The plan', eyebrow: `${plan.passes.length} PASSES · ${fmtBytes(plan.size_bytes)}`,
      right: raw(chip(plan.sanitize_supported ? 'ok' : 'warn',
                      plan.sanitize_supported ? 'SANITIZE AVAILABLE' : 'NO SANITIZE')),
      children: table(
        ['#', 'Pass', 'Kind', 'Detail', 'Est.'],
        plan.passes.map((p) => [
          raw(`<span class="mono">${p.n}</span>`),
          raw(`<span class="mono">${esc(p.kind)}</span>`),
          raw(chip(PASS_KIND_CHIP[p.kind] || 'idle', p.kind)),
          esc(p.detail),
          raw(`<span class="micro">${fmtDur(p.est_s)}</span>`),
        ])) + (plan.notes || []).map((n) =>
          `<p class="micro" style="color:var(--amber)">${esc(n)}</p>`).join(''),
    });
    renderSafety();
  }

  function renderSafety() {
    const blockers = plan.blockers || [];
    const dev = devices.find((d) => d.path === form.device);
    $('#sn-safety').innerHTML = card({
      num: '03', title: 'Safety', eyebrow: 'READ THIS PART TWICE',
      right: raw(chip(blockers.length ? 'err' : 'ok',
                      blockers.length ? 'BLOCKED' : 'CLEAR TO CONFIRM')),
      children: `
        <p>${esc(edu.whyTypedDestroy.body)}</p>
        ${blockers.length ? `<div style="margin-top:0.7rem">${
          blockers.map((b) => `<p class="micro" style="color:var(--danger)">• ${esc(b)}</p>`).join('')
        }</div>` : ''}
        <div style="margin-top:1rem" id="sn-confirm-block">
          <p class="micro" style="margin-bottom:0.5rem">TYPE <strong>DESTROY</strong> AND THE DEVICE NAME <strong>${esc(form.device)}</strong> TO ARM</p>
          <div class="confirmbox">
            <input class="finput" id="sn-confirm-word" placeholder="DESTROY" maxlength="10" spellcheck="false">
            <input class="finput" id="sn-confirm-device" placeholder="${esc(form.device)}" spellcheck="false">
            ${btn({ label: 'DESTROY THE DRIVE', kind: 'danger', id: 'sn-confirm-go',
                    tip: 'Starts the root worker via pkexec' })}
          </div>
        </div>
        <p class="micro" style="margin-top:0.9rem;color:var(--ink-4)">${esc(edu.mountedRefusal.headline)}: ${esc(edu.mountedRefusal.body)}</p>`,
    });
    const go = $('#sn-confirm-go');
    const check = () => {
      const word = $('#sn-confirm-word').value.trim().toUpperCase();
      const devc = $('#sn-confirm-device').value.trim();
      go.setAttribute('aria-disabled',
        blockers.length || word !== 'DESTROY' || devc !== form.device ? 'true' : 'false');
    };
    $('#sn-confirm-word').addEventListener('input', check);
    $('#sn-confirm-device').addEventListener('input', check);
    go.setAttribute('aria-disabled', 'true');
    go.addEventListener('click', doStart);
  }

  async function doStart() {
    form.confirm_word = $('#sn-confirm-word').value.trim().toUpperCase();
    form.confirm_device = $('#sn-confirm-device').value.trim();
    const ack = await api.wipeStart({
      device: form.device, verify: form.verify, quick: form.quick,
      unmount: form.unmount,
      confirm_word: form.confirm_word, confirm_device: form.confirm_device,
    });
    $('#sn-run-section').hidden = false;
    stream.clear();
    if (!ack.ok) {
      stream.append(`[ERROR] ${ack.error || 'start refused'}`);
      return;
    }
    stream.append('[INFO] pkexec: authorize when the system dialog appears');
    renderPasses([]);
  }

  function renderPasses(progressByPass) {
    if (!plan) return;
    $('#sn-progress').innerHTML = plan.passes.map((p) => {
      const prog = progressByPass[p.n];
      const pct = prog != null ? Math.round(prog) : (progressByPass.done?.includes(p.n) ? 100 : 0);
      return `<div class="plane"><div class="plane-top">` +
        `<span class="plane-label">PASS ${p.n} · ${esc(p.kind.toUpperCase())}: ${esc(p.detail)}</span>` +
        `<span class="plane-pct">${pct}%</span></div>` +
        `<div class="plane-track"><i style="width:${pct}%"></i></div></div>`;
    }).join('') + `
      <div style="margin-top:0.8rem;display:flex;gap:0.6rem">
        ${btn({ label: 'Cancel the wipe', kind: 'danger', id: 'sn-cancel',
                tip: 'Stops at the next checkpoint; a sanitize in flight is aborted with sanact=0' })}
      </div>`;
    $('#sn-cancel').onclick = () => api.wipeAbort();
  }

  const passProgress = {};
  subs.push(bus.on('line', (l) => {
    if (l.channel === 'wipe.log') {
      if ($('#sn-run-section').hidden) $('#sn-run-section').hidden = false;
      stream.append(l.text);
      return;
    }
    if (l.channel !== 'wipe') return;
    let ev;
    try { ev = JSON.parse(l.text); } catch { return; }
    if (ev.event === 'pass_start') {
      passProgress[ev.passno] = 0;
      renderPasses(passProgress);
    } else if (ev.event === 'pass_progress' || ev.event === 'sanitize_progress') {
      passProgress[ev.passno] = ev.percent ?? 0;
      renderPasses(passProgress);
    } else if (ev.event === 'verify_progress') {
      passProgress[ev.passno] = (ev.checked / ev.of) * 100;
      renderPasses(passProgress);
    } else if (ev.event === 'done') {
      passProgress.done = ev.passes_done || [];
      renderPasses(passProgress);
    }
  }));

  subs.push(bus.on('report', ({ channel, data }) => {
    if (channel !== 'wipe.summary') return;
    stream.append(`[DONE] ${data.cancelled ? 'cancelled' : (data.ok ? 'wipe complete' : 'wipe ended with problems')}`);
  }));

  devices = await api.deviceList();
  devices = Array.isArray(devices) ? devices : [];
  if (!devices.length) {
    root.innerHTML = pageHeader({ exit: '02', title: 'Sanitize', lede: edu.copy.sanitizeLede }) +
      emptyState({
        code: '02', headline: 'No devices to sanitize',
        guidance: 'ATTACH A DRIVE AND VISIT EXIT 01 · DRIVES',
      });
    return () => subs.forEach((off) => off());
  }
  if (!form.device) {
    form.device = (devices.find((d) => !d.os_disk) || devices[0]).path;
  }
  renderForm();
  await buildPlan();

  return () => subs.forEach((off) => off());
}
