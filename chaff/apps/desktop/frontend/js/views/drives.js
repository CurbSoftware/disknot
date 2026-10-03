// Chafftafarian: views/drives.js
// EXIT 01 · DRIVES. Attached disks, what each one is, inspect on demand.
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

  root.innerHTML = `
    ${pageHeader({ exit: '01', title: 'Drives', lede: edu.copy.drivesLede,
                   coords: 'LSBLK · ID-CTRL · SMART' })}
    <div class="status-moment" style="margin-bottom:1rem">
      <span id="dr-chip"></span>
      <span class="grow"></span>
      ${btn({ label: 'Rescan', kind: 'ghost', id: 'dr-refresh', tip: 'Re-run lsblk and capability probes' })}
    </div>
    <div id="dr-list" class="card-grid"></div>
    <div id="dr-inspect"></div>
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
  }

  function sanitizeChip(dev) {
    if (!dev.is_nvme) return raw(chip('idle', 'NOT NVME'));
    const s = dev.sanitize;
    if (!s) return raw(chip('warn', 'PROBE FAILED'));
    if (!s.supported) return raw(chip('err', 'SANITIZE UNSUPPORTED'));
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
      const go = dev.os_disk ? '' : `
        ${btn({ label: 'Sanitize this drive', kind: 'danger', id: `dr-go-${dev.kname}`,
                tip: 'Open Sanitize with this device selected' })}`;
      return card({
        num: (dev.kname || '').replace(/[^0-9a-z]/gi, '').slice(0, 2) || 'DK',
        title: dev.model || dev.kname,
        eyebrow: `${dev.path} · ${(dev.transport || 'local').toUpperCase()}`,
        right: raw(
          (dev.os_disk ? chip('err', 'OS DISK') : '') +
          (dev.swap ? chip('err', 'SWAP') : '') +
          (dev.mounted ? chip('warn', 'MOUNTED') : chip('idle', 'UNMOUNTED'))),
        children: `
          <p class="fact">${esc(edu.driveIs(dev))}</p>
          <p class="fact">${esc(edu.driveRole(dev))}</p>
          <p class="fact">${esc(edu.sanitizeDoes(dev))}</p>
          ${parts}
          ${kv([
            ['SIZE', raw(`<span class="mono">${edu.fmtBytes(dev.size_bytes)}</span>`)],
            ['SERIAL', raw(`<span class="mono">${esc(dev.serial || '·')}</span>`)],
            ['SANITIZE', sanitizeChip(dev)],
          ])}
          <div style="margin-top:0.8rem;display:flex;gap:0.6rem;flex-wrap:wrap">
            ${btn({ label: 'Inspect', id: `dr-in-${dev.kname}`,
                    tip: 'Read last sanitize status and SMART' })}
            ${go}
          </div>`,
      });
    }).join('');

    devices.forEach((dev) => {
      $(`#dr-in-${dev.kname}`)?.addEventListener('click', () => inspect(dev));
      if (!dev.os_disk) {
        $(`#dr-go-${dev.kname}`)?.addEventListener('click', () => {
          state.pendingDevice = dev.path;
          location.hash = '#/sanitize';
        });
      }
    });
  }

  async function inspect(dev) {
    inspecting = dev.path;
    $('#dr-inspect').innerHTML = card({
      num: 'i', title: 'Inspect', eyebrow: dev.path,
      children: '<p class="dim">Reading sanitize-log and SMART.</p>',
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
      right: raw(sanChip + smartChip),
      children: `
        <p class="fact">${esc(edu.driveIs(out))}</p>
        <p class="fact">${esc(edu.driveRole(out))}</p>
        <p class="fact">${esc(edu.sanitizeDoes(out))}</p>
        <p class="fact">${esc(edu.lastSanitizeIs(out.last_sanitize))}</p>
        <p class="fact">${esc(edu.smartIs(out.smart, out.smart_error))}</p>
        ${parts}
        ${kv([
          ['SERIAL', raw(`<span class="mono">${esc(out.serial || '·')}</span>`)],
          ['FIRMWARE', raw(`<span class="mono">${esc(out.smart?.firmware || '·')}</span>`)],
          ['TRANSPORT', raw(`<span class="mono">${esc(out.transport || '·')}</span>`)],
        ])}`,
    });
    $('#dr-inspect').scrollIntoView({ block: 'nearest' });
  }

  async function refresh() {
    $('#dr-refresh').setAttribute('aria-disabled', 'true');
    devices = await api.deviceList();
    devices = Array.isArray(devices) ? devices : [];
    renderChip();
    renderList();
    $('#dr-refresh').setAttribute('aria-disabled', 'false');
  }

  $('#dr-refresh').addEventListener('click', refresh);
  subs.push(bus.on('state', renderChip));
  await refresh();

  return () => subs.forEach((off) => off());
}
