// Disknot: views/drives.js
// EXIT 01 · DRIVES. Attached disks, types (ssd, hdd, nvme with/without reset),
// detailed inspection, multi-drive selection with OS disk protection.
// Payload shapes: BRIDGE.md (deviceList, deviceDetail).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, kv, card, btn, pageHeader, emptyState, raw, esc,
} from '../components/ui.js';
import * as edu from '../content.js';

export async function render(root) {
  const subs = [];
  let devices = [];
  let inspecting = null;
  const selected = new Set(state.pendingDevices || (state.pendingDevice ? [state.pendingDevice] : []));

  root.innerHTML = `
    ${pageHeader({ exit: '01', title: 'Drives', lede: edu.copy.drivesLede,
                   coords: 'SCAN · DISK TYPES · INSPECT' })}
    <div class="status-moment" style="margin-bottom:1rem;display:flex;align-items:center;gap:0.8rem;flex-wrap:wrap">
      <span id="dr-chip"></span>
      <span id="dr-sel-chip"></span>
      <span class="grow"></span>
      ${btn({ label: 'Sanitize Selected Drives', kind: 'danger', id: 'dr-sanitize-sel', disabled: true, tip: 'Open Sanitize pipeline with selected drives' })}
      ${btn({ label: 'Rescan', kind: 'ghost', id: 'dr-refresh', tip: 'Re-run lsblk, nvme, and capability probes' })}
    </div>
    <div id="dr-list" class="card-grid"></div>
    <div id="dr-inspect" style="margin-top:1.5rem"></div>
  `;

  const $ = (sel) => root.querySelector(sel);

  function renderChip() {
    const el = $('#dr-chip');
    if (!devices.length) {
      el.innerHTML = chip('idle', 'NO DISKS SEEN', { lg: true });
    } else {
      const missing = state.toolsMissing;
      el.innerHTML = missing.length
        ? chip('warn', `TOOLS MISSING: ${missing.join(' · ').toUpperCase()}`, { lg: true })
        : chip('ok', `${devices.length} DISK${devices.length > 1 ? 'S' : ''}`, { lg: true });
    }
    updateSelectionToolbar();
  }

  function updateSelectionToolbar() {
    const selEl = $('#dr-sel-chip');
    const goBtn = $('#dr-sanitize-sel');
    if (!selEl || !goBtn) return;
    const count = selected.size;
    if (count > 0) {
      selEl.innerHTML = chip('ok', `${count} SELECTED FOR SANITIZE`);
      goBtn.disabled = false;
      goBtn.classList.remove('ghost');
    } else {
      selEl.innerHTML = chip('idle', 'SELECT DRIVES TO SANITIZE');
      goBtn.disabled = true;
    }
  }

  function diskTypeChip(dev) {
    if (dev.os_disk) return raw(chip('err', 'OS DISK · LOCKED'));
    const t = dev.disk_type || (dev.is_nvme ? (dev.has_reset ? 'nvme_with_reset' : 'nvme_no_reset') : (dev.rotational ? 'hdd' : 'ssd'));
    if (t === 'nvme_with_reset') return raw(chip('ok', 'NVME (WITH RESET)'));
    if (t === 'nvme_no_reset') return raw(chip('warn', 'NVME (NO RESET)'));
    if (t === 'ssd') return raw(chip('ok', 'SSD (SOLID STATE)'));
    if (t === 'hdd') return raw(chip('idle', 'HDD (ROTATIONAL)'));
    if (t === 'usb') return raw(chip('idle', 'USB DRIVE'));
    return raw(chip('idle', 'DISK'));
  }

  function sanitizeChip(dev) {
    if (!dev.is_nvme) {
      if (dev.ata) {
        if (dev.ata.frozen) return raw(chip('err', 'ATA FROZEN'));
        if (dev.ata.sanitize_block || dev.ata.security_erase) return raw(chip('ok', 'ATA FIRMWARE ERASE'));
      }
      return raw(chip('idle', dev.rotational ? 'OVERWRITE ONLY' : 'OVERWRITE / ATA'));
    }
    const s = dev.sanitize;
    if (!s) return raw(chip('warn', 'PROBE FAILED'));
    if (!s.supported) return raw(chip('warn', 'NO HARDWARE RESET'));
    const methods = [
      s.block_erase && 'BLOCK ERASE',
      s.crypto_erase && 'CRYPTO',
      s.overwrite && 'OVERWRITE',
    ].filter(Boolean);
    return raw(chip('ok', methods.join(' + ')));
  }

  function renderList() {
    if (!devices.length) {
      $('#dr-list').innerHTML = emptyState({
        code: '01',
        headline: 'No disks visible',
        guidance: 'ATTACH A DRIVE AND RESCAN, OR CHECK PERMISSIONS',
      });
      return;
    }
    $('#dr-list').innerHTML = devices.map((dev) => {
      const parts = (dev.partitions || []).map((p) =>
        `<p class="fact">${esc(edu.partitionIs(p))}</p>`).join('');

      const isSel = selected.has(dev.path);
      const isOs = !!dev.os_disk;

      const checkbox = isOs
        ? `<label class="dr-check" title="OS Disk cannot be sanitized" style="opacity:0.6;cursor:not-allowed">
             <input type="checkbox" disabled> <span class="micro" style="color:var(--danger)">PROTECTED (OS DISK)</span>
           </label>`
        : `<label class="dr-check" style="display:flex;align-items:center;gap:0.4rem;cursor:pointer">
             <input type="checkbox" id="dr-chk-${dev.kname}" data-path="${esc(dev.path)}"${isSel ? ' checked' : ''}>
             <span class="micro">${isSel ? 'SELECTED' : 'SELECT DRIVE'}</span>
           </label>`;

      const singleGo = isOs ? '' : `
        ${btn({ label: 'Configure Sanitize', kind: 'danger', id: `dr-go-${dev.kname}`,
                tip: 'Open Sanitize pipeline with this drive' })}`;

      return card({
        num: (dev.kname || '').replace(/[^0-9a-z]/gi, '').slice(0, 2) || 'DK',
        title: dev.model || dev.kname,
        eyebrow: `${dev.path} · ${(dev.transport || 'local').toUpperCase()}`,
        right: raw(
          diskTypeChip(dev) + ' ' +
          (dev.swap ? chip('err', 'SWAP') : '') +
          (dev.mounted ? chip('warn', 'MOUNTED') : chip('idle', 'UNMOUNTED'))),
        children: `
          <div style="margin-bottom:0.6rem;padding:0.4rem 0.6rem;background:var(--well);border-radius:4px;display:flex;align-items:center;justify-content:space-between">
            ${checkbox}
            <span class="micro mono">${esc(edu.diskTypeLabel(dev))}</span>
          </div>
          <p class="fact">${esc(edu.driveIs(dev))}</p>
          <p class="fact">${esc(edu.driveRole(dev))}</p>
          <p class="fact">${esc(edu.sanitizeDoes(dev))}</p>
          ${parts}
          ${kv([
            ['TYPE', raw(`<span class="mono">${esc(edu.diskTypeLabel(dev))}</span>`)],
            ['SIZE', raw(`<span class="mono">${edu.fmtBytes(dev.size_bytes)}</span>`)],
            ['SERIAL', raw(`<span class="mono">${esc(dev.serial || '·')}</span>`)],
            ['RESET / WIPE', sanitizeChip(dev)],
          ])}
          <div style="margin-top:0.8rem;display:flex;gap:0.6rem;flex-wrap:wrap;align-items:center">
            ${btn({ label: 'Inspect', id: `dr-in-${dev.kname}`,
                    tip: 'Read last sanitize status and SMART' })}
            ${singleGo}
          </div>`,
      });
    }).join('');

    devices.forEach((dev) => {
      $(`#dr-in-${dev.kname}`)?.addEventListener('click', () => inspect(dev));
      if (!dev.os_disk) {
        $(`#dr-go-${dev.kname}`)?.addEventListener('click', () => {
          state.pendingDevice = dev.path;
          state.pendingDevices = [dev.path];
          location.hash = '#/sanitize';
        });

        $(`#dr-chk-${dev.kname}`)?.addEventListener('change', (e) => {
          if (e.target.checked) {
            selected.add(dev.path);
          } else {
            selected.delete(dev.path);
          }
          state.pendingDevices = Array.from(selected);
          updateSelectionToolbar();
        });
      }
    });

    $('#dr-sanitize-sel')?.addEventListener('click', () => {
      if (!selected.size) return;
      state.pendingDevices = Array.from(selected);
      state.pendingDevice = state.pendingDevices[0] || null;
      location.hash = '#/sanitize';
    });
  }

  async function inspect(dev) {
    inspecting = dev.path;
    $('#dr-inspect').innerHTML = card({
      num: 'i', title: 'Inspect', eyebrow: dev.path,
      children: '<p class="dim">Reading sanitize-log, SMART, and device capabilities.</p>',
    });
    $('#dr-inspect').scrollIntoView({ block: 'nearest' });
    const out = await api.deviceDetail(dev.path);
    if (inspecting !== dev.path) return;
    if (out.error) {
      $('#dr-inspect').innerHTML = card({
        num: 'i', title: 'Inspect', eyebrow: 'REFUSED',
        children: `<p class="fact">${esc(out.error)}</p>`,
      });
      $('#dr-inspect').scrollIntoView({ block: 'nearest' });
      return;
    }
    const smartChip = out.smart_error
      ? chip('warn', 'SMART UNAVAILABLE')
      : (out.smart?.passed === false ? chip('err', 'SMART FAILED')
        : (out.smart?.passed ? chip('ok', 'SMART PASSED') : chip('idle', 'SMART')));
    const sanChip = !out.last_sanitize
      ? chip('idle', 'NO SANITIZE LOG')
      : chip(
        out.last_sanitize.state === 'success' ? 'ok'
          : (out.last_sanitize.state === 'failed' || out.last_sanitize.state === 'in-progress' ? 'warn' : 'idle'),
        `LAST: ${(out.last_sanitize.state || 'unknown').toUpperCase()}`,
      );
    const parts = (out.partitions || []).map((p) =>
      `<p class="fact">${esc(edu.partitionIs(p))}</p>`).join('');
    $('#dr-inspect').innerHTML = card({
      num: 'i', title: out.model || out.kname,
      eyebrow: (out.path || '').toUpperCase(),
      right: raw(diskTypeChip(out) + ' ' + sanChip + ' ' + smartChip),
      children: `
        <p class="fact">${esc(edu.driveIs(out))}</p>
        <p class="fact">${esc(edu.driveRole(out))}</p>
        <p class="fact">${esc(edu.sanitizeDoes(out))}</p>
        <p class="fact">${esc(edu.lastSanitizeIs(out.last_sanitize))}</p>
        <p class="fact">${esc(edu.smartIs(out.smart, out.smart_error))}</p>
        ${parts}
        ${kv([
          ['DISK TYPE', raw(`<span class="mono">${esc(edu.diskTypeLabel(out))}</span>`)],
          ['SERIAL', raw(`<span class="mono">${esc(out.serial || '·')}</span>`)],
          ['FIRMWARE', raw(`<span class="mono">${esc(out.smart?.firmware || '·')}</span>`)],
          ['TRANSPORT', raw(`<span class="mono">${esc(out.transport || '·')}</span>`)],
          ['ROTATIONAL', raw(`<span class="mono">${out.rotational ? 'YES (HDD)' : 'NO (SSD/Flash)'}</span>`)],
        ])}`,
    });
    $('#dr-inspect').scrollIntoView({ block: 'nearest' });
  }

  async function refresh() {
    $('#dr-refresh').setAttribute('aria-disabled', 'true');
    devices = await api.deviceList();
    devices = Array.isArray(devices) ? devices : [];
    // Ensure OS disk cannot be in selected
    devices.forEach((d) => {
      if (d.os_disk) selected.delete(d.path);
    });
    renderChip();
    renderList();
    $('#dr-refresh').setAttribute('aria-disabled', 'false');
  }

  $('#dr-refresh').addEventListener('click', refresh);
  subs.push(bus.on('state', renderChip));
  await refresh();

  return () => subs.forEach((off) => off());
}
