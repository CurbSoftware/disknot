// Chafftafarian: views/drives.js
// EXIT 01 · DRIVES. The drive bay: what is attached, what it can survive,
// and what it is doing right now. Payload shapes: BRIDGE.md (deviceList).

import { api } from '../api.js';
import { state, bus } from '../shell.js';
import {
  chip, kv, card, btn, pageHeader, emptyState, raw, esc,
} from '../components/ui.js';

const fmtBytes = (n) => {
  if (n == null) return '·';
  let f = Number(n);
  for (const u of ['B', 'KiB', 'MiB', 'GiB', 'TiB']) {
    if (f < 1024 || u === 'TiB') return u === 'B' ? `${f}${f % 1 ? '' : ''}${u}` : `${f.toFixed(f >= 100 ? 0 : 1)}${u}`;
    f /= 1024;
  }
  return `${n}B`;
};

export async function render(root) {
  const subs = [];
  let devices = [];

  root.innerHTML = `
    ${pageHeader({ exit: '01', title: 'Drives', lede: 'The drive bay. What is attached, what it can survive, and what it is doing right now.',
                   coords: 'LSBLK · ID-CTRL' })}
    <div class="status-moment" style="margin-bottom:1rem">
      <span id="dr-chip"></span>
      <span class="grow"></span>
      ${btn({ label: 'Rescan', kind: 'ghost', id: 'dr-refresh', tip: 'Re-run lsblk and capability probes' })}
    </div>
    <div id="dr-list" class="card-grid"></div>
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

  function sanitizeChips(dev) {
    if (!dev.is_nvme) return raw(chip('idle', 'NO SANITIZE (NOT NVME)'));
    const s = dev.sanitize;
    if (!s) return raw(chip('warn', 'PROBE FAILED'));
    if (!s.supported) return raw(chip('err', 'SANITIZE UNSUPPORTED'));
    const methods = [
      s.block_erase && 'BLOCK ERASE',
      s.crypto_erase && 'CRYPTO',
      s.overwrite && 'OVERWRITE',
    ].filter(Boolean);
    return raw(chip('ok', `SANITIZE: ${methods.join(' + ')}`));
  }

  function renderList() {
    if (!devices.length) {
      $('#dr-list').innerHTML = emptyState({
        code: '01',
        headline: 'No disks visible',
        guidance: 'LSBLK REPORTED NO WHOLE DISKS: ATTACH A DRIVE AND RESCAN, OR CHECK PERMISSIONS',
      });
      return;
    }
    $('#dr-list').innerHTML = devices.map((dev) => card({
      num: dev.kname.replace(/[^0-9a-z]/gi, '').slice(0, 2) || 'DK',
      title: dev.model || dev.kname,
      eyebrow: `${dev.path} · ${(dev.transport || 'local').toUpperCase()}`,
      right: raw(
        (dev.os_disk ? chip('err', 'OS DISK') : '') +
        (dev.swap ? chip('err', 'SWAP') : '') +
        (dev.mounted ? chip('warn', 'MOUNTED') : chip('idle', 'UNMOUNTED'))),
      children: kv([
        ['SIZE', raw(`<span class="mono">${fmtBytes(dev.size_bytes)}</span>`)],
        ['SERIAL', raw(`<span class="mono">${esc(dev.serial || '·')}</span>`)],
        ['MOUNTS', raw(`<span class="mono">${dev.mountpoints?.length ? esc(dev.mountpoints.join(' · ')) : '·'}</span>`)],
        ['SANITIZE', sanitizeChips(dev)],
      ]) + (dev.os_disk
        ? '<p class="micro" style="color:var(--danger)">THIS DISK HOSTS THE RUNNING OS: THE SANITIZE VIEW WILL ALWAYS REFUSE IT</p>'
        : `<div style="margin-top:0.8rem">
             ${btn({ label: 'Sanitize this drive…', kind: 'danger', id: `dr-go-${dev.kname}`,
                     tip: 'Open the SANITIZE view with this device preselected' })}
           </div>`),
    })).join('');

    devices.filter((d) => !d.os_disk).forEach((dev) => {
      $(`#dr-go-${dev.kname}`)?.addEventListener('click', () => {
        state.pendingDevice = dev.path;
        location.hash = '#/sanitize';
      });
    });
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
