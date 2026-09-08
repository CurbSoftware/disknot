// Chafftafarian: views/settings.js
// EXIT 05 · SETTINGS. The small set of persisted, safety-relevant knobs.
// Payload shapes: BRIDGE.md (settingsGet / settingsSave).

import { api } from '../api.js';
import { bus } from '../shell.js';
import { chip, card, btn, pageHeader, kv, raw, esc } from '../components/ui.js';

export async function render(root) {
  const subs = [];
  let data = null;
  const values = {};

  root.innerHTML = `
    ${pageHeader({ exit: '05', title: 'Settings', lede: 'The few knobs that persist: each one narrows or widens what the app is allowed to touch.',
                   coords: 'SAFETY · PERSISTED' })}
    <div class="card-grid">
      <div id="st-form"></div>
      <div id="st-side"></div>
    </div>
  `;

  const $ = (sel) => root.querySelector(sel);

  function renderForm() {
    const groups = new Map();
    for (const spec of data.specs.device) {
      if (!groups.has(spec.group)) groups.set(spec.group, []);
      groups.get(spec.group).push(spec);
    }
    $('#st-form').innerHTML = card({
      num: '05', title: 'Device settings', eyebrow: 'PERSISTED IN APP.JSON',
      children: [...groups.entries()].map(([group, specs]) => `
        <p class="micro" style="margin:0.4rem 0 0.8rem">${esc(group.toUpperCase())}</p>
        <div class="fgrid">
        ${specs.map((s) => `
          <div class="ffield">
            <label>${esc(s.label || s.name)}</label>
            ${s.kind === 'bool'
              ? `<label style="display:flex;gap:0.5rem;align-items:center">
                   <input type="checkbox" data-setting="${esc(s.name)}"${values[s.name] === 'true' ? ' checked' : ''}>
                   enabled</label>`
              : `<input class="finput" data-setting="${esc(s.name)}" value="${esc(values[s.name] ?? s.default)}" spellcheck="false">`}
            ${s.help ? `<span class="fhint">${esc(s.help)}</span>` : ''}
          </div>`).join('')}
        </div>`).join('') + `
        <div style="margin-top:1rem">
          ${btn({ label: 'Save', kind: 'primary', id: 'st-save' })}
          <span class="micro" id="st-status" style="margin-left:0.8rem"></span>
        </div>`,
    });
    root.querySelectorAll('[data-setting]').forEach((el) => {
      const handler = () => {
        values[el.dataset.setting] = el.type === 'checkbox'
          ? (el.checked ? 'true' : 'false') : el.value;
      };
      el.addEventListener(el.type === 'checkbox' ? 'change' : 'input', handler);
    });
    $('#st-save').addEventListener('click', save);
  }

  async function save() {
    const status = $('#st-status');
    status.textContent = 'SAVING…';
    const out = await api.settingsSave(JSON.stringify({ device: values }));
    status.textContent = out.ok ? 'SAVED' : `REFUSED: ${(out.errors || []).join('; ')}`;
    status.style.color = out.ok ? 'var(--amber)' : 'var(--danger)';
    if (out.ok) {
      data = await api.settingsGet();
      Object.assign(values, data.values.device);
    }
  }

  $('#st-side').innerHTML = card({
    num: 'i', title: 'Where things live', eyebrow: 'ON THIS MACHINE',
    children: kv([
      ['APP SETTINGS', raw('<span class="mono">~/.config/chafftafarian/app.json</span>')],
      ['WIPE LOGS', raw('<span class="mono">~/.local/state/chafftafarian/</span>')],
      ['CHAFF RUNS', raw('<span class="mono">the target directory you pick, one Chaff_Run_* per run</span>')],
      ['VENDORED CORE', raw('<span class="mono">apps/desktop/src/chaff_generator (see VENDORED.md)</span>')],
    ]) + `
      <p class="micro" style="margin-top:0.9rem;color:var(--ink-4)">The allow-non-NVMe switch exists so the wipe path can be exercised on loop devices. Everything else about device safety is decided by the drive bay, not by settings.</p>`,
  });

  data = await api.settingsGet();
  Object.assign(values, data.values.device);
  renderForm();

  return () => subs.forEach((off) => off());
}
