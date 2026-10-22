// Disknot: views/sanitize.js
// EXIT 02 · SANITIZE. Multi-drive pipeline, modular disk tools,
// predefined sequence presets (e.g. laptop selling), arming & execution.
// Payload shapes: BRIDGE.md (pipelinePresets, pipelinePlan, wipeStart, wipeAbort).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, card, btn, pageHeader, sectionHead, emptyState, table, raw, esc,
} from '../components/ui.js';
import { logStream } from '../components/progress.js';
import * as edu from '../content.js';

const fmtDur = (s) => (s == null ? 'unknown' : s >= 5400 ? `~${Math.round(s / 3600)} h` : `~${Math.round(s / 60)} min`);

const STEP_KIND_CHIP = {
  shred: 'warn',
  nvme_reset: 'ok',
  ata_erase: 'ok',
  zeros: 'idle',
  ones: 'idle',
  overwrite_x: 'warn',
  chaff_create: 'ok',
  chaff_delete: 'idle',
  verify: 'warn',
  sanitize: 'ok',
};

export async function render(root) {
  const subs = [];
  let devices = [];
  let toolsCatalog = { tools: [], presets: [] };

  // Determine selected drives
  let selectedPaths = state.pendingDevices && state.pendingDevices.length
    ? [...state.pendingDevices]
    : (state.pendingDevice ? [state.pendingDevice] : []);

  state.pendingDevices = null;
  state.pendingDevice = null;

  let currentPreset = 'laptop_sale';
  let pipelinePlan = null;
  let unmountOpt = true;

  root.innerHTML = `
    ${pageHeader({ exit: '02', title: 'Sanitize', lede: edu.copy.sanitizeLede,
                   coords: 'MULTI-DRIVE · COMPOSABLE TOOLS · IRREVERSIBLE' })}
    <div class="card-grid" style="grid-template-columns:1fr">
      <div id="sn-drives-picker"></div>
    </div>
    <div style="margin-top:1rem" id="sn-preset-bar"></div>
    <div id="sn-pipeline-cards" style="margin-top:1rem;display:flex;flex-direction:column;gap:1.2rem"></div>
    <div id="sn-safety" style="margin-top:1.5rem"></div>
    <section class="section" id="sn-run-section" hidden style="margin-top:1.5rem">
      ${sectionHead({ num: 'W', title: 'Sanitization in progress', meta: '' })}
      <div id="sn-target-badge" style="margin-bottom:0.8rem"></div>
      <div id="sn-progress"></div>
      <div id="sn-next" hidden style="margin-top:0.8rem"></div>
      <div id="sn-log" style="margin-top:0.8rem"></div>
    </section>
  `;

  const $ = (sel) => root.querySelector(sel);
  const stream = logStream($('#sn-log'));

  function renderDrivesPicker() {
    const selectable = devices.filter((d) => !d.os_disk);
    const osDisks = devices.filter((d) => d.os_disk);

    const checkItems = selectable.map((d) => {
      const isSel = selectedPaths.includes(d.path);
      return `
        <label style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.6rem;background:var(--well);border-radius:4px;cursor:pointer">
          <input type="checkbox" data-path="${esc(d.path)}"${isSel ? ' checked' : ''} class="sn-drv-chk">
          <span class="mono" style="font-weight:600">${esc(d.path)}</span>
          <span class="micro">${esc(d.model || d.kname)} (${edu.fmtBytes(d.size_bytes)})</span>
          <span class="grow"></span>
          ${raw(chip(isSel ? 'ok' : 'idle', edu.diskTypeLabel(d)))}
        </label>`;
    }).join('');

    const osItems = osDisks.map((d) => `
      <div style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.6rem;background:var(--well);opacity:0.6;border-radius:4px">
        <input type="checkbox" disabled>
        <span class="mono" style="font-weight:600">${esc(d.path)}</span>
        <span class="micro">${esc(d.model || d.kname)} (${edu.fmtBytes(d.size_bytes)})</span>
        <span class="grow"></span>
        <span class="chip err"><span class="dot"></span>OS DISK · LOCKED</span>
      </div>`).join('');

    $('#sn-drives-picker').innerHTML = card({
      num: '01', title: 'Target Drives Selection', eyebrow: `${selectedPaths.length} DRIVES SELECTED`,
      children: `
        <p class="fact">Select the drives you wish to sanitize. OS drives hosting the live system cannot be selected.</p>
        <div style="display:flex;flex-direction:column;gap:0.4rem;margin:0.8rem 0">
          ${checkItems || '<p class="dim">No non-OS drives available.</p>'}
          ${osItems}
        </div>
        <label style="display:flex;gap:0.5rem;align-items:center;margin-top:0.6rem;cursor:pointer">
          <input type="checkbox" id="sn-unmount-all"${unmountOpt ? ' checked' : ''}> unmount partitions for me
        </label>
      `,
    });

    root.querySelectorAll('.sn-drv-chk').forEach((chk) => {
      chk.addEventListener('change', async (e) => {
        const p = e.target.dataset.path;
        if (e.target.checked) {
          if (!selectedPaths.includes(p)) selectedPaths.push(p);
        } else {
          selectedPaths = selectedPaths.filter((x) => x !== p);
        }
        await loadPreset(currentPreset);
      });
    });

    $('#sn-unmount-all')?.addEventListener('change', (e) => {
      unmountOpt = e.target.checked;
      buildPlan();
    });
  }

  function renderPresetBar() {
    const presetsList = [
      { id: 'laptop_sale', label: 'Selling Laptop (Recommended)' },
      { id: 'paranoid_6pass', label: 'Paranoid 6-Pass' },
      { id: 'nist_800_88_purge', label: 'NIST SP 800-88 Purge' },
      { id: 'quick_wipe', label: 'Quick Wipe' },
      { id: 'chaff_conceal', label: 'Chaff & Conceal' },
      { id: 'custom', label: 'Custom Sequence' },
    ];

    const buttons = presetsList.map((pr) => `
      <button type="button" data-preset="${pr.id}" class="${currentPreset === pr.id ? 'on' : ''}">${pr.label}</button>
    `).join('');

    $('#sn-preset-bar').innerHTML = card({
      num: '02', title: 'Sequence Preset Template', eyebrow: 'PREDEFINED OR CUSTOM STEPS',
      children: `
        <div class="seg" id="sn-preset-seg" style="flex-wrap:wrap">
          ${buttons}
        </div>
        <p class="fhint" style="margin-top:0.6rem">${esc(edu.PRESET_DOES[currentPreset] || '')}</p>
      `,
    });

    $('#sn-preset-seg')?.addEventListener('click', async (e) => {
      const b = e.target.closest('button[data-preset]');
      if (!b) return;
      currentPreset = b.dataset.preset;
      renderPresetBar();
      await loadPreset(currentPreset);
    });
  }

  async function loadPreset(presetName) {
    if (!selectedPaths.length) {
      pipelinePlan = { targets: [], blockers: ['No drives selected.'], confirmation: { word: 'DESTROY', devices: [] } };
      renderPipelineCards();
      renderSafety();
      return;
    }
    const res = await api.pipelinePresets(presetName, JSON.stringify(selectedPaths));
    pipelinePlan = typeof res === 'string' ? JSON.parse(res) : res;
    renderPipelineCards();
    renderSafety();
  }

  function renderPipelineCards() {
    const container = $('#sn-pipeline-cards');
    if (!container) return;
    if (!pipelinePlan || !pipelinePlan.targets || !pipelinePlan.targets.length) {
      container.innerHTML = card({
        num: '03', title: 'Pipeline Steps', eyebrow: 'NO TARGETS',
        children: '<p class="dim">Select one or more drives above to configure sanitization steps.</p>',
      });
      return;
    }

    container.innerHTML = pipelinePlan.targets.map((t, tIdx) => {
      const dev = devices.find((d) => d.path === t.device) || {};
      const typeLabel = edu.diskTypeLabel(dev);

      const stepsHtml = (t.steps || []).map((s, sIdx) => {
        const chipKind = STEP_KIND_CHIP[s.kind] || 'idle';
        const badge = chip(chipKind, s.kind.toUpperCase().replace('_', ' '));
        const unsuppWarn = !s.supported && s.unsupported_reason
          ? `<span class="micro" style="color:var(--danger)">${esc(s.unsupported_reason)}</span>`
          : '';

        return `
          <div style="display:flex;align-items:center;gap:0.8rem;padding:0.6rem 0.8rem;background:var(--well);border-radius:4px;margin-bottom:0.4rem">
            <span class="mono" style="font-weight:700">#${s.n}</span>
            ${badge}
            <div style="display:flex;flex-direction:column;flex:1">
              <span class="fact" style="margin:0">${esc(s.detail)}</span>
              ${unsuppWarn}
            </div>
            <span class="micro">${fmtDur(s.est_s)}</span>
            <div style="display:flex;gap:0.2rem">
              <button type="button" class="btn ghost btn-step-up" data-tidx="${tIdx}" data-sidx="${sIdx}" ${sIdx === 0 ? 'disabled' : ''} title="Move Step Up">▲</button>
              <button type="button" class="btn ghost btn-step-dn" data-tidx="${tIdx}" data-sidx="${sIdx}" ${sIdx === t.steps.length - 1 ? 'disabled' : ''} title="Move Step Down">▼</button>
              <button type="button" class="btn danger btn-step-del" data-tidx="${tIdx}" data-sidx="${sIdx}" title="Remove Step">✕</button>
            </div>
          </div>
        `;
      }).join('');

      // Add Step Options
      const isNvmeWithReset = dev.disk_type === 'nvme_with_reset' || (dev.is_nvme && dev.has_reset);
      const isSata = !dev.is_nvme;

      const addOptions = [
        '<option value="">+ Add Step to this Drive...</option>',
        '<option value="shred">Shred (Random Overwrite)</option>',
        isNvmeWithReset
          ? '<option value="nvme_reset">NVMe Reset / Hardware Sanitize</option>'
          : '<option value="nvme_reset" disabled>NVMe Reset (Not supported on this drive)</option>',
        isSata
          ? '<option value="ata_erase">ATA Firmware Erase</option>'
          : '<option value="ata_erase" disabled>ATA Erase (NVMe drives use NVMe Reset)</option>',
        '<option value="zeros">Write Zeros (0x00)</option>',
        '<option value="ones">Write Ones (0xFF)</option>',
        '<option value="overwrite_x">Overwrite X Times (Multi-pass)</option>',
        '<option value="chaff_create">Create Chaff (Synthetic Decoy Files)</option>',
        '<option value="chaff_delete">Delete Chaff</option>',
        '<option value="verify">Verify (Sampled Read-Back)</option>',
      ].join('');

      return card({
        num: `03.${tIdx + 1}`,
        title: `${t.device} · ${t.model || 'Disk'}`,
        eyebrow: `${typeLabel.toUpperCase()} · ${edu.fmtBytes(t.size_bytes)}`,
        right: raw(chip('ok', `${t.steps.length} STEPS`)),
        children: `
          <div style="margin-bottom:0.8rem">
            ${stepsHtml || '<p class="dim">No steps configured for this drive.</p>'}
          </div>
          <div style="display:flex;align-items:center;gap:0.8rem">
            <select class="finput sn-add-step-select" data-tidx="${tIdx}" style="max-width:320px">
              ${addOptions}
            </select>
          </div>
        `,
      });
    }).join('');

    // Attach step control listeners
    container.querySelectorAll('.btn-step-up').forEach((b) => {
      b.addEventListener('click', (e) => {
        const tIdx = Number(b.dataset.tidx);
        const sIdx = Number(b.dataset.sidx);
        const steps = pipelinePlan.targets[tIdx].steps;
        if (sIdx > 0) {
          const temp = steps[sIdx - 1];
          steps[sIdx - 1] = steps[sIdx];
          steps[sIdx] = temp;
          currentPreset = 'custom';
          renderPresetBar();
          buildPlan();
        }
      });
    });

    container.querySelectorAll('.btn-step-dn').forEach((b) => {
      b.addEventListener('click', (e) => {
        const tIdx = Number(b.dataset.tidx);
        const sIdx = Number(b.dataset.sidx);
        const steps = pipelinePlan.targets[tIdx].steps;
        if (sIdx < steps.length - 1) {
          const temp = steps[sIdx + 1];
          steps[sIdx + 1] = steps[sIdx];
          steps[sIdx] = temp;
          currentPreset = 'custom';
          renderPresetBar();
          buildPlan();
        }
      });
    });

    container.querySelectorAll('.btn-step-del').forEach((b) => {
      b.addEventListener('click', (e) => {
        const tIdx = Number(b.dataset.tidx);
        const sIdx = Number(b.dataset.sidx);
        pipelinePlan.targets[tIdx].steps.splice(sIdx, 1);
        currentPreset = 'custom';
        renderPresetBar();
        buildPlan();
      });
    });

    container.querySelectorAll('.sn-add-step-select').forEach((sel) => {
      sel.addEventListener('change', (e) => {
        const kind = e.target.value;
        if (!kind) return;
        const tIdx = Number(sel.dataset.tidx);
        const steps = pipelinePlan.targets[tIdx].steps;
        steps.push({
          n: steps.length + 1,
          kind: kind,
          detail: edu.TOOL_DOES[kind] || `Execute ${kind}`,
          supported: true,
        });
        currentPreset = 'custom';
        renderPresetBar();
        buildPlan();
      });
    });
  }

  async function buildPlan() {
    if (!pipelinePlan || !pipelinePlan.targets) return;
    const cfg = {
      targets: pipelinePlan.targets,
      preset: currentPreset,
      unmount: unmountOpt,
    };
    const res = await api.pipelinePlan(JSON.stringify(cfg));
    pipelinePlan = typeof res === 'string' ? JSON.parse(res) : res;
    renderPipelineCards();
    renderSafety();
  }

  function renderSafety() {
    const blockers = pipelinePlan ? (pipelinePlan.blockers || []) : ['No plan built.'];
    const targets = pipelinePlan ? (pipelinePlan.targets || []) : [];
    const devicesList = targets.map((t) => t.device).join(', ');

    $('#sn-safety').innerHTML = card({
      num: '04', title: 'Safety & Destruction Confirmation', eyebrow: 'ARMING',
      right: raw(chip(blockers.length ? 'err' : 'ok', blockers.length ? 'BLOCKED' : 'READY TO ARM')),
      children: `
        <p class="fact">${esc(edu.whyTypedDestroy.body)}</p>
        ${blockers.length ? `<div style="margin-top:0.7rem">${
          blockers.map((b) => `<p class="fact" style="color:var(--danger)">${esc(b)}</p>`).join('')
        }</div>` : ''}
        <div style="margin-top:1rem" id="sn-confirm-block">
          <p class="fact" style="margin-bottom:0.5rem">
            Type <strong>DESTROY</strong> to permanently wipe: <span class="mono">${esc(devicesList || 'no drives')}</span>
          </p>
          <div class="confirmbox">
            <input class="finput" id="sn-confirm-word" placeholder="DESTROY" maxlength="10" spellcheck="false">
            ${btn({ label: 'DESTROY SELECTED DRIVES', kind: 'danger', id: 'sn-confirm-go',
                    tip: 'Starts the root worker via pkexec to execute the pipeline' })}
          </div>
        </div>
      `,
    });

    const go = $('#sn-confirm-go');
    const check = () => {
      const word = $('#sn-confirm-word').value.trim().toUpperCase();
      go.setAttribute('aria-disabled',
        blockers.length || word !== 'DESTROY' || !targets.length ? 'true' : 'false');
    };
    $('#sn-confirm-word')?.addEventListener('input', check);
    go?.setAttribute('aria-disabled', 'true');
    go?.addEventListener('click', doStart);
  }

  async function doStart() {
    const word = $('#sn-confirm-word').value.trim().toUpperCase();
    const targetDevs = pipelinePlan.targets.map((t) => t.device);

    const ackStr = await api.wipeStart(JSON.stringify({
      targets: pipelinePlan.targets,
      confirm_word: word,
      confirm_devices: targetDevs,
      unmount: unmountOpt,
    }));

    const ack = typeof ackStr === 'string' ? JSON.parse(ackStr) : ackStr;
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
    stream.append('[INFO] pkexec: authorize when the system prompt appears');
    renderExecutionProgress(0, 0, {});
  }

  function renderExecutionProgress(targetIdx, passNo, progMap) {
    const targets = pipelinePlan ? (pipelinePlan.targets || []) : [];
    const targetBadgeEl = $('#sn-target-badge');
    if (targetBadgeEl) {
      if (targets.length > 1) {
        targetBadgeEl.innerHTML = `<span class="chip ok">TARGET ${targetIdx || 1} OF ${targets.length}</span>`;
      } else {
        targetBadgeEl.innerHTML = '';
      }
    }

    let allPasses = [];
    targets.forEach((t) => {
      (t.steps || []).forEach((s) => allPasses.push({ ...s, device: t.device }));
    });

    $('#sn-progress').innerHTML = allPasses.map((p, idx) => {
      const passNum = idx + 1;
      const pct = progMap[passNum] ?? (progMap.done?.includes(passNum) ? 100 : 0);
      return `
        <div class="plane">
          <div class="plane-top">
            <span class="plane-label">[${esc(p.device)}] PASS ${passNum} · ${esc(p.kind.toUpperCase())}: ${esc(p.detail)}</span>
            <span class="plane-pct">${Math.round(pct)}%</span>
          </div>
          <div class="plane-track"><i style="width:${Math.round(pct)}%"></i></div>
        </div>`;
    }).join('') + `
      <div style="margin-top:0.8rem;display:flex;gap:0.6rem">
        ${btn({ label: 'Cancel Pipeline', kind: 'danger', id: 'sn-cancel-pipe',
                tip: 'Stops at the next block checkpoint or pass boundary' })}
      </div>`;

    $('#sn-cancel-pipe')?.addEventListener('click', () => api.wipeAbort());
  }

  const liveProgress = { currentTarget: 1 };
  subs.push(bus.on('line', (l) => {
    if (l.channel === 'wipe.log') {
      if ($('#sn-run-section').hidden) $('#sn-run-section').hidden = false;
      stream.append(l.text);
      return;
    }
    if (l.channel !== 'wipe') return;
    let ev;
    try { ev = JSON.parse(l.text); } catch { return; }

    if (ev.event === 'target_start') {
      liveProgress.currentTarget = ev.target_index;
      renderExecutionProgress(ev.target_index, 0, liveProgress);
    } else if (ev.event === 'pass_start') {
      liveProgress[ev.passno] = 0;
      renderExecutionProgress(liveProgress.currentTarget, ev.passno, liveProgress);
    } else if (ev.event === 'pass_progress' || ev.event === 'sanitize_progress') {
      liveProgress[ev.passno] = ev.percent ?? 0;
      renderExecutionProgress(liveProgress.currentTarget, ev.passno, liveProgress);
    } else if (ev.event === 'verify_progress') {
      liveProgress[ev.passno] = (ev.checked / ev.of) * 100;
      renderExecutionProgress(liveProgress.currentTarget, ev.passno, liveProgress);
    } else if (ev.event === 'done') {
      liveProgress.done = ev.passes_done || [];
      renderExecutionProgress(liveProgress.currentTarget, 0, liveProgress);
    }
  }));

  subs.push(bus.on('report', ({ channel, data }) => {
    if (channel !== 'wipe.summary') return;
    const ok = data.ok;
    const canc = data.cancelled;
    stream.append(`[DONE] ${canc ? 'Pipeline cancelled by user' : (ok ? 'Pipeline completed successfully' : 'Pipeline ended with issues')}`);
    const next = $('#sn-next');
    if (next) {
      next.hidden = false;
      next.innerHTML = card({
        num: 'N', title: 'Pipeline Finished', eyebrow: 'STATUS',
        children: `<p class="fact">${canc ? 'Operation was cancelled.' : (ok ? 'All configured drives have been sanitized according to their respective step pipelines.' : 'Check logs for any errors.')}</p>`,
      });
    }
  }));

  // Bootstrap data
  const rawList = await api.deviceList();
  devices = Array.isArray(rawList) ? rawList : (typeof rawList === 'string' ? JSON.parse(rawList) : []);

  if (!selectedPaths.length) {
    const firstNonOs = devices.find((d) => !d.os_disk);
    if (firstNonOs) selectedPaths.push(firstNonOs.path);
  }

  renderDrivesPicker();
  renderPresetBar();
  await loadPreset(currentPreset);

  return () => subs.forEach((off) => off());
}
