// Chafftafarian: views/sanitize.js
// EXIT 02 · SANITIZE. Plan it, type DESTROY plus the device name, watch
// every pass. Payload shapes: BRIDGE.md (wipePlan, wipeStart).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, card, btn, pageHeader, sectionHead, emptyState, table, raw, esc,
} from '../components/ui.js';
import { logStream } from '../components/progress.js';
import * as edu from '../content.js';

const fmtDur = (s) => (s == null ? 'unknown' : s >= 5400 ? `~${Math.round(s / 3600)} h` : `~${Math.round(s / 60)} min`);

const PASS_KIND_CHIP = { sanitize: 'ok', zeros: 'idle', ones: 'idle', verify: 'warn' };

export async function render(root) {
  const subs = [];
  let devices = [];
  let plan = null;
  const form = {
    device: state.pendingDevice || '', verify: false, plan: 'paranoid',
    sanitize_action: 'block-erase', unmount: false,
    confirm_word: '', confirm_device: '',
  };
  state.pendingDevice = null;

  root.innerHTML = `
    ${pageHeader({ exit: '02', title: 'Sanitize', lede: edu.copy.sanitizeLede,
                   coords: 'IRREVERSIBLE' })}
    <div class="card-grid">
      <div id="sn-form"></div>
      <div id="sn-plan"></div>
    </div>
    <div id="sn-safety"></div>
    <section class="section" id="sn-run-section" hidden>
      ${sectionHead({ num: 'W', title: 'Wipe in progress', meta: '' })}
      <div id="sn-progress"></div>
      <div id="sn-next" hidden style="margin-top:0.8rem"></div>
      <div id="sn-log" style="margin-top:0.8rem"></div>
    </section>
  `;

  const $ = (sel) => root.querySelector(sel);
  const stream = logStream($('#sn-log'));

  function selectedDev() {
    return devices.find((d) => d.path === form.device);
  }

  function renderForm() {
    const dev = selectedDev();
    const nvme = !!dev?.is_nvme;
    const cryptoOk = !!(dev?.sanitize?.crypto_erase);
    const overwriteOk = !!(dev?.sanitize?.overwrite);
    const ata = dev?.ata || null;
    const sataBlockOk = !!(ata && ata.sanitize_block && !ata.frozen);
    const sataCryptoOk = !!(ata && ata.sanitize_crypto && !ata.frozen);
    const sataSecureOk = !!(ata && ata.security_erase && !ata.frozen);
    if (nvme) {
      if (form.sanitize_action === 'crypto-erase' && !cryptoOk) form.sanitize_action = 'block-erase';
      if (form.sanitize_action === 'overwrite' && !overwriteOk) form.sanitize_action = 'block-erase';
      if (String(form.sanitize_action).startsWith('sata-')) form.sanitize_action = 'block-erase';
    } else if (dev) {
      if (!String(form.sanitize_action).startsWith('sata-')) {
        form.sanitize_action = sataBlockOk ? 'sata-block-erase'
          : (sataCryptoOk ? 'sata-crypto' : 'sata-block-erase');
      }
    }
    const options = ['<option value="">choose a device</option>']
      .concat(devices.map((d) =>
        `<option value="${esc(d.path)}"${d.path === form.device ? ' selected' : ''}>` +
        `${esc(d.path)}: ${esc(d.model || d.kname)} (${edu.fmtBytes(d.size_bytes)})` +
        `${d.os_disk ? ' · OS DISK' : ''}</option>`));
    const anySata = sataBlockOk || sataCryptoOk || sataSecureOk;
    const methodHint = nvme
      ? (edu.ACTION_DOES[form.sanitize_action] || 'This drive does not support that sanitize method.')
      : (anySata && edu.ACTION_DOES[form.sanitize_action]
        ? edu.ACTION_DOES[form.sanitize_action]
        : edu.ataIs(ata));
    const methodRow = nvme
      ? `<div class="seg" id="sn-action">
            <button type="button" data-action="block-erase" class="${form.sanitize_action === 'block-erase' ? 'on' : ''}">block erase</button>
            <button type="button" data-action="crypto-erase" class="${form.sanitize_action === 'crypto-erase' ? 'on' : ''}"
              ${cryptoOk ? '' : 'disabled'}>crypto erase</button>
            <button type="button" data-action="overwrite" class="${form.sanitize_action === 'overwrite' ? 'on' : ''}"
              ${overwriteOk ? '' : 'disabled'}>overwrite</button>
          </div>`
      : `<div class="seg" id="sn-action">
            <button type="button" data-action="sata-block-erase" class="${form.sanitize_action === 'sata-block-erase' ? 'on' : ''}"
              ${sataBlockOk ? '' : 'disabled'}>block erase</button>
            <button type="button" data-action="sata-crypto" class="${form.sanitize_action === 'sata-crypto' ? 'on' : ''}"
              ${sataCryptoOk ? '' : 'disabled'}>crypto scramble</button>
            <button type="button" data-action="sata-secure-erase" class="${form.sanitize_action === 'sata-secure-erase' ? 'on' : ''}"
              ${sataSecureOk ? '' : 'disabled'}>security erase</button>
          </div>`;
    $('#sn-form').innerHTML = card({
      num: '01', title: 'Target and plan', eyebrow: 'WHAT WILL BE DESTROYED',
      children: `<div class="fgrid">
        <div class="ffield"><label>Device</label>
          <select class="finput" id="sn-device">${options.join('')}</select>
          ${dev ? `<span class="fhint">${esc(edu.driveIs(dev))} ${esc(edu.driveRole(dev))}</span>` : ''}
        </div>
        <div class="ffield"><label>Plan</label>
          <div class="seg" id="sn-planmode">
            <button type="button" data-plan="paranoid" class="${form.plan === 'paranoid' ? 'on' : ''}">paranoid</button>
            <button type="button" data-plan="standard" class="${form.plan === 'standard' ? 'on' : ''}">standard</button>
            <button type="button" data-plan="quick" class="${form.plan === 'quick' ? 'on' : ''}">quick</button>
          </div>
          <span class="fhint">${esc(edu.PLAN_DOES[form.plan])}</span>
        </div>
        <div class="ffield"><label>Sanitize method</label>
          ${methodRow}
          <span class="fhint">${esc(methodHint)}</span>
        </div>
        <div class="ffield"><label>Options</label>
          <label style="display:flex;gap:0.5rem;align-items:center">
            <input type="checkbox" id="sn-verify"${form.verify ? ' checked' : ''}> verify after</label>
          <span class="fhint">${esc(edu.OPTION_DOES.verify)}</span>
          <label style="display:flex;gap:0.5rem;align-items:center;margin-top:0.6rem">
            <input type="checkbox" id="sn-unmount"${form.unmount ? ' checked' : ''}> unmount partitions for me</label>
          <span class="fhint">${esc(edu.OPTION_DOES.unmount)}</span>
        </div>
      </div>
      <div style="margin-top:1rem">
        ${btn({ label: 'Build the plan', kind: 'primary', id: 'sn-plan-btn',
                tip: 'Probe capabilities and compute passes. Writes nothing.' })}
      </div>`,
    });
    $('#sn-device').addEventListener('change', async (e) => {
      form.device = e.target.value;
      await enrichSelected();
      renderForm();
      buildPlan();
    });
    $('#sn-planmode').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-plan]');
      if (!b) return;
      form.plan = b.dataset.plan;
      renderForm();
      buildPlan();
    });
    $('#sn-action').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-action]');
      if (!b || b.disabled) return;
      form.sanitize_action = b.dataset.action;
      renderForm();
      buildPlan();
    });
    $('#sn-verify').addEventListener('change', (e) => { form.verify = e.target.checked; buildPlan(); });
    $('#sn-unmount').addEventListener('change', (e) => { form.unmount = e.target.checked; buildPlan(); });
    $('#sn-plan-btn').addEventListener('click', buildPlan);
  }

  async function enrichSelected() {
    const dev = selectedDev();
    if (!dev || dev.is_nvme || dev.ata !== undefined) return;
    const out = await api.deviceDetail(dev.path);
    if (out && !out.error) {
      dev.ata = out.ata || null;
    } else {
      dev.ata = null;
    }
  }

  function planConfig() {
    return {
      device: form.device,
      verify: form.verify,
      plan: form.plan,
      quick: form.plan === 'quick',
      unmount: form.unmount,
      sanitize_action: form.sanitize_action,
    };
  }

  async function buildPlan() {
    if (!form.device) return;
    plan = await api.wipePlan(planConfig());
    renderPlan();
  }

  function renderPlan() {
    if (!plan) return;
    if (plan.error) {
      $('#sn-plan').innerHTML = card({
        num: '02', title: 'Plan', eyebrow: 'REFUSED',
        children: `<p class="fact">${esc(plan.error)}</p>` });
      $('#sn-safety').innerHTML = '';
      return;
    }
    $('#sn-plan').innerHTML = card({
      num: '02', title: 'The plan', eyebrow: `${plan.passes.length} PASSES · ${edu.fmtBytes(plan.size_bytes)}`,
      right: raw(chip(plan.sanitize_supported ? 'ok' : 'warn',
                      plan.sanitize_supported ? 'SANITIZE AVAILABLE' : 'NO SANITIZE')),
      children: table(
        ['#', 'Pass', 'Kind', 'This pass will', 'Est.'],
        plan.passes.map((p) => [
          raw(`<span class="mono">${p.n}</span>`),
          raw(`<span class="mono">${esc(p.kind)}</span>`),
          raw(chip(PASS_KIND_CHIP[p.kind] || 'idle', p.kind)),
          esc(p.detail),
          raw(`<span class="micro">${fmtDur(p.est_s)}</span>`),
        ])) + (plan.notes || []).map((n) =>
          `<p class="fact" style="color:var(--amber)">${esc(n)}</p>`).join(''),
    });
    renderSafety();
  }

  function renderSafety() {
    const blockers = plan.blockers || [];
    $('#sn-safety').innerHTML = card({
      num: '03', title: 'Safety', eyebrow: 'ARMING',
      right: raw(chip(blockers.length ? 'err' : 'ok',
                      blockers.length ? 'BLOCKED' : 'CLEAR TO CONFIRM')),
      children: `
        <p class="fact">${esc(edu.whyTypedDestroy.body)}</p>
        ${blockers.length ? `<div style="margin-top:0.7rem">${
          blockers.map((b) => `<p class="fact" style="color:var(--danger)">${esc(b)}</p>`).join('')
        }</div>` : ''}
        <div style="margin-top:1rem" id="sn-confirm-block">
          <p class="fact" style="margin-bottom:0.5rem">${esc(edu.armDestroy(form.device))}</p>
          <div class="confirmbox">
            <input class="finput" id="sn-confirm-word" placeholder="DESTROY" maxlength="10" spellcheck="false">
            <input class="finput" id="sn-confirm-device" placeholder="${esc(form.device)}" spellcheck="false">
            ${btn({ label: 'DESTROY THE DRIVE', kind: 'danger', id: 'sn-confirm-go',
                    tip: 'Starts the root worker via pkexec' })}
          </div>
        </div>
        <p class="fact" style="margin-top:0.9rem">${esc(edu.mountedRefusal.body)}</p>
        <p class="micro" style="margin:1rem 0 0.4rem;color:var(--ink-4)">IF THIS HAPPENS</p>
        ${edu.troubleshooting.map((t) =>
          `<p class="fact"><strong>${esc(t.happen)}</strong> ${esc(t.do)}</p>`).join('')}`,
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
      ...planConfig(),
      confirm_word: form.confirm_word, confirm_device: form.confirm_device,
    });
    $('#sn-run-section').hidden = false;
    const next = $('#sn-next');
    if (next) {
      next.hidden = true;
      next.innerHTML = '';
    }
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
                tip: 'Stops at the next checkpoint. A sanitize in flight is aborted with sanact=0.' })}
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
    const next = $('#sn-next');
    if (!next) return;
    next.hidden = false;
    next.innerHTML = card({
      num: 'N', title: 'Next', eyebrow: 'AFTER THE WIPE',
      children: `<p class="fact">${esc(edu.afterWipeIs(data, plan))}</p>`,
    });
  }));

  devices = await api.deviceList();
  devices = Array.isArray(devices) ? devices : [];
  if (!devices.length) {
    root.innerHTML = pageHeader({ exit: '02', title: 'Sanitize', lede: edu.copy.sanitizeLede }) +
      emptyState({
        code: '02', headline: 'No devices to sanitize',
        guidance: 'ATTACH A DRIVE AND OPEN EXIT 01 DRIVES',
      });
    return () => subs.forEach((off) => off());
  }
  if (!form.device) {
    form.device = (devices.find((d) => !d.os_disk) || devices[0]).path;
  }
  renderForm();
  await enrichSelected();
  renderForm();
  await buildPlan();

  return () => subs.forEach((off) => off());
}
