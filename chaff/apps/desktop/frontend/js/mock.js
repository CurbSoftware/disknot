// Chafftafarian: mock.js
// Stands in for the QWebChannel bridge when ?mock=1 (or plain browser dev).
// Payload shapes mirror BRIDGE.md 1:1 (the frozen contract) so views built
// against the mock behave identically against Qt. Scenario data is
// synthetic: two NVMe drives (one hosting the OS), one USB stick, one loop
// device, a couple of historical chaff runs.
//
// Signals (same normalisation as api.js applies in Qt):
//   lineReady    {channel:'chaff'|'chaff.log'|'wipe'|'wipe.log'|'tail', text}
//   reportReady  {channel:'chaff.summary'|'wipe.summary'|'wipe.plan'|
//                        'chaff.preflight'|'verify'|'probe', data}
//   stateChanged {busy, kind:'chaff'|'wipe'|null, started, core_version, tools}

const listeners = new Map();

export function on(signal, fn) {
  if (!listeners.has(signal)) listeners.set(signal, []);
  listeners.get(signal).push(fn);
  return () => {
    const arr = listeners.get(signal);
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  };
}

function emit(signal, payload) {
  for (const fn of [...(listeners.get(signal) || [])]) fn(payload);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => '[' + new Date().toISOString().slice(0, 19).replace('T', ' ') + ']';
const clone = (v) => JSON.parse(JSON.stringify(v));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TOOLS = {
  nvme: '/usr/sbin/nvme',
  pkexec: '/usr/bin/pkexec',
  lsblk: '/usr/bin/lsblk',
  findmnt: '/usr/bin/findmnt',
  smartctl: '/usr/sbin/smartctl',
  hdparm: '/usr/sbin/hdparm',
};

const BOOTSTRAP = {
  core: { version: '0.1.0', ready: true },
  tools: { ...TOOLS },
  busy: false,
  kind: null,
  app_settings: { allow_non_nvme: false, default_target: '/srv/chaff' },
  disk: { target: { free_gb: 33.1, used_gb: 48.9, total_gb: 82, tree_bytes: 402653184 } },
};

const DEVICES = [
  {
    kname: 'nvme0n1', path: '/dev/nvme0n1', type: 'disk', transport: 'nvme',
    size_bytes: 2000398934016, model: 'Samsung SSD 990 PRO 2TB',
    serial: 'S6Z1NJ0R123456', is_nvme: true,
    sanitize: { supported: true, block_erase: true, crypto_erase: true, overwrite: false },
    partitions: [
      { name: 'nvme0n1p1', mountpoints: ['/boot/efi'], fstype: 'vfat', size_bytes: 536866816 },
      { name: 'nvme0n1p2', mountpoints: ['/'], fstype: 'ext4', size_bytes: 1988163260416 },
    ],
    os_disk: true, mounted: true, swap: false,
  },
  {
    kname: 'nvme1n1', path: '/dev/nvme1n1', type: 'disk', transport: 'nvme',
    size_bytes: 1000204886016, model: 'WD_BLACK SN770 1TB',
    serial: 'WDF0101ABCDEF', is_nvme: true,
    sanitize: { supported: true, block_erase: true, crypto_erase: false, overwrite: true },
    partitions: [
      { name: 'nvme1n1p1', mountpoints: ['/mnt/scratch'], fstype: 'ext4', size_bytes: 1000202039296 },
    ],
    os_disk: false, mounted: true, swap: false,
  },
  {
    kname: 'sda', path: '/dev/sda', type: 'disk', transport: 'usb',
    size_bytes: 30752073728, model: 'Cruzer Glide 3.0',
    serial: '030112214ABC', is_nvme: false,
    sanitize: null,
    partitions: [],
    os_disk: false, mounted: false, swap: false,
  },
  {
    kname: 'sdb', path: '/dev/sdb', type: 'disk', transport: 'sata',
    size_bytes: 500107862016, model: 'Samsung SSD 860 EVO 500GB',
    serial: 'S3Z1NB0K123456', is_nvme: false,
    sanitize: null,
    partitions: [],
    os_disk: false, mounted: false, swap: false,
  },
  {
    kname: 'loop0', path: '/dev/loop0', type: 'disk', transport: null,
    size_bytes: 1073741824, model: '(loop)', serial: '',
    is_nvme: false,
    sanitize: null,
    partitions: [],
    os_disk: false, mounted: false, swap: false,
  },
];

const SMART = {
  '/dev/nvme0n1': {
    smart: { passed: true, temperature_c: 48, power_on_hours: 6122,
             firmware: '4B2QJXD7', percentage_used: 41, available_spare: 100, media_errors: 0 },
    smart_error: null,
    last_sanitize: { state: 'never', media_modified: false, percent: 0 },
  },
  '/dev/nvme1n1': {
    smart: { passed: true, temperature_c: 36, power_on_hours: 1404,
             firmware: '731030WD', percentage_used: 3, available_spare: 100, media_errors: 0 },
    smart_error: null,
    last_sanitize: { state: 'in-progress', media_modified: false, percent: 41 },
  },
  '/dev/sda': {
    smart: null, smart_error: 'permission', last_sanitize: null, ata: null,
  },
  '/dev/sdb': {
    smart: { passed: true, temperature_c: 33, power_on_hours: 8801,
             firmware: 'RVT04B6Q', percentage_used: null, available_spare: null, media_errors: null },
    smart_error: null,
    last_sanitize: null,
    ata: { frozen: true, sanitize_block: true, sanitize_crypto: true,
           security_erase: true, security_erase_enhanced: true },
  },
  '/dev/loop0': {
    smart: null, smart_error: 'failed', last_sanitize: null, ata: null,
  },
};

const RUNS = [
  {
    run_id: '20260905_142233_a3f1', root: '/srv/chaff/Chaff_Run_20260905_142233_a3f1',
    created_at: '2026-09-05T14:22:33Z', status: 'completed',
    files: 1284, bytes_written: 6442450944, profile: 'realistic-desktop',
    seed: 1317, verified: 'INTACT', verified_at: '2026-09-05T15:01:44Z',
  },
  {
    run_id: '20260901_091502_77ba', root: '/srv/chaff/Chaff_Run_20260901_091502_77ba',
    created_at: '2026-09-01T09:15:02Z', status: 'completed',
    files: 412, bytes_written: 2147483648, profile: 'storage-test',
    seed: 90210, verified: 'PROBLEMS', verified_at: '2026-09-01T09:40:12Z',
  },
  {
    run_id: '20260828_220001_04cd', root: '/srv/chaff/Chaff_Run_20260828_220001_04cd',
    created_at: '2026-08-28T22:00:01Z', status: 'cancelled',
    files: 96, bytes_written: 402653184, profile: 'office-workstation',
    seed: 0, verified: null, verified_at: null,
  },
];

const LOGS = [
  { engine: 'wipe', name: 'wipe-20260904_101155.log', path: '/var/log/chafftafarian/wipe-20260904_101155.log', size_bytes: 8123, mtime: '2026-09-04T11:02:41Z' },
  { engine: 'chaff', name: 'chaff-20260905_142233.log', path: '/srv/chaff/Chaff_Run_20260905_142233_a3f1/run.log', size_bytes: 64100, mtime: '2026-09-05T15:01:39Z' },
];

const SPEC_DEVICE = [
  { name: 'allow_non_nvme', default: 'false', kind: 'bool', choices: [], group: 'Devices',
    label: 'Allow non-NVMe devices',
    help: 'This switch lets wipe run on SATA, USB, and loop devices. Leave it off unless you are testing.' },
];

// ---------------------------------------------------------------------------
// Simulator state
// ---------------------------------------------------------------------------

const state = { busy: false, kind: null, started: null };
let devices = clone(DEVICES);
let runs = clone(RUNS);
let timers = [];

function setState(patch) {
  Object.assign(state, patch);
  emit('stateChanged', { busy: state.busy, kind: state.kind, started: state.started,
                         core_version: '0.1.0', tools: { ...TOOLS } });
}

const line = (channel, text) => emit('lineReady', { channel, text });
const report = (channel, data) => emit('reportReady', { channel, data });
const at = (ms, fn) => timers.push(setTimeout(fn, ms));
function clearTimers() {
  for (const t of timers) clearTimeout(t);
  timers = [];
}

// -- bootstrap ---------------------------------------------------------------

export async function getBootstrap() {
  return clone(BOOTSTRAP);
}

// -- devices -----------------------------------------------------------------

export async function deviceList() {
  return clone(devices);
}

export async function deviceDetail(pathOrKname) {
  const d = devices.find((x) =>
    x.path === pathOrKname || x.kname === pathOrKname || x.kname === String(pathOrKname).replace(/^\/dev\//, ''));
  if (!d) return { error: `no device ${pathOrKname}` };
  const extra = SMART[d.path] || { smart: null, smart_error: 'failed', last_sanitize: null };
  return clone({ ...d, ...extra });
}

// -- wipe ----------------------------------------------------------------------

function resolvePlanName(cfg) {
  if (cfg.plan === 'paranoid' || cfg.plan === 'standard' || cfg.plan === 'quick') return cfg.plan;
  return cfg.quick ? 'quick' : 'paranoid';
}

function sanitizeActionFor(dev, requested) {
  if (!dev.is_nvme) {
    const ata = (SMART[dev.path] || {}).ata;
    if (!ata || ata.frozen) return null;
    if (requested === 'sata-secure-erase' && ata.security_erase) return 'sata-secure-erase';
    if (requested === 'sata-crypto' && ata.sanitize_crypto) return 'sata-crypto';
    if (requested === 'sata-block-erase' && ata.sanitize_block) return 'sata-block-erase';
    if (ata.sanitize_block) return 'sata-block-erase';
    if (ata.sanitize_crypto) return 'sata-crypto';
    return null;
  }
  const want = (requested === 'crypto-erase' || requested === 'overwrite' || requested === 'block-erase')
    ? requested : 'block-erase';
  if (want === 'crypto-erase' && dev.sanitize?.crypto_erase) return 'crypto-erase';
  if (want === 'overwrite' && dev.sanitize?.overwrite) return 'overwrite';
  if (want === 'block-erase' && dev.sanitize?.block_erase) return 'block-erase';
  if (dev.sanitize?.block_erase) return 'block-erase';
  if (dev.sanitize?.crypto_erase) return 'crypto-erase';
  if (dev.sanitize?.overwrite) return 'overwrite';
  return null;
}

function sanitizeDetail(action, second) {
  const which = second ? 'a second NVMe Sanitize' : 'NVMe Sanitize';
  if (action === 'crypto-erase') {
    return `This pass will run ${which} (crypto erase). The controller destroys its media encryption key.`;
  }
  if (action === 'overwrite') {
    return `This pass will run ${which} (overwrite). The controller overwrites every block with its sanitize pattern.`;
  }
  if (action === 'sata-block-erase') {
    const w = second ? 'a second ATA sanitize' : 'ATA sanitize';
    return `This pass will run ${w} (block erase) via hdparm. The controller erases every block.`;
  }
  if (action === 'sata-crypto') {
    const w = second ? 'a second ATA sanitize' : 'ATA sanitize';
    return `This pass will run ${w} (crypto scramble) via hdparm. The controller destroys its media encryption key.`;
  }
  if (action === 'sata-secure-erase') {
    return 'This pass will run ATA security erase via hdparm. This sets a temporary password p, then erases. If it is interrupted, the drive may stay locked.';
  }
  return `This pass will run ${which} (block erase). The controller erases every block.`;
}

export function wipePlan(config) {
  const cfg = typeof config === 'string' ? JSON.parse(config || '{}') : (config || {});
  const dev = devices.find((x) => x.path === cfg.device || x.kname === cfg.device);
  if (!dev) return { error: `no device ${cfg.device}` };
  const name = resolvePlanName(cfg);
  const action = sanitizeActionFor(dev, cfg.sanitize_action);
  const writeEst = Math.round(dev.size_bytes / 200e6);
  const passes = [];
  const notes = [];
  const add = (kind, detail, est_s, act) => {
    passes.push({ n: passes.length + 1, kind, detail, est_s, action: act || null });
  };
  const extra = SMART[dev.path] || {};
  if (extra.last_sanitize?.state === 'in-progress') {
    notes.push('A sanitize is already in progress. Starting this wipe will abort it first (sanact=0), then run this plan.');
  }
  if (!dev.is_nvme && extra.ata?.frozen) {
    notes.push('This drive is frozen. Firmware erase is refused. Power-cycle the drive. This app will not suspend the machine to thaw it. Overwrite passes still run.');
  }
  if (action) {
    add('sanitize', sanitizeDetail(action, false), null, action);
  } else if (dev.is_nvme) {
    notes.push('This controller reports no sanitize support. Overwrite passes only. Remapped cells may retain data.');
  } else if (!extra.ata?.frozen) {
    notes.push('This drive has no firmware erase. Overwrite passes only. Remapped cells may retain data.');
  }
  if (name === 'quick') {
    add('zeros', 'This pass will overwrite the whole disk with zeros.', writeEst);
  } else if (name === 'standard') {
    add('zeros', 'This pass will overwrite the whole disk with zeros.', writeEst);
    add('ones', 'This pass will overwrite the whole disk with 0xFF.', writeEst);
  } else {
    add('zeros', 'This pass will overwrite the whole disk with zeros.', writeEst);
    add('ones', 'This pass will overwrite the whole disk with 0xFF.', writeEst);
    add('zeros', 'This pass will overwrite the whole disk with zeros again.', writeEst);
    if (action) add('sanitize', sanitizeDetail(action, true), null, action);
    add('zeros', 'This pass will overwrite the whole disk with zeros. The drive is left zeroed.', writeEst);
  }
  if (cfg.verify) {
    add('verify', 'This pass will read a sample of blocks and check they match zeros.', 120);
  }
  const blockers = [];
  if (dev.os_disk) blockers.push('this disk hosts the running operating system');
  if (dev.mounted && !cfg.unmount) blockers.push('mounted partitions present');
  if (dev.swap) blockers.push('swap active on this disk');
  if (!dev.is_nvme && !BOOTSTRAP.app_settings.allow_non_nvme) {
    blockers.push('not an NVMe device (enable in settings to override)');
  }
  const firmware = dev.is_nvme ? !!dev.sanitize?.supported : !!(extra.ata && extra.ata.firmware_supported !== false && (extra.ata.sanitize_block || extra.ata.sanitize_crypto || extra.ata.security_erase));
  return { device: dev.path, model: dev.model, size_bytes: dev.size_bytes,
           passes, notes, blockers, sanitize_supported: firmware,
           confirmation: { word: 'DESTROY', device: dev.path } };
}

export async function wipeStart(config) {
  const cfg = typeof config === 'string' ? JSON.parse(config || '{}') : (config || {});
  if (state.busy) return { ok: false, error: 'busy' };
  const plan = wipePlan(clone(cfg));
  if (plan.error) return { ok: false, error: plan.error };
  if (plan.blockers.length) return { ok: false, error: plan.blockers.join('; ') };
  if (cfg.confirm_word !== 'DESTROY' || cfg.confirm_device !== cfg.device) {
    return { ok: false, error: 'confirmation mismatch' };
  }

  setState({ busy: true, kind: 'wipe', started: new Date().toISOString() });
  line('wipe.log', `${now()} [START] wipe of ${cfg.device} (${plan.passes.length} passes)`);

  let cancelled = false;
  mock.abortWipe = () => {
    cancelled = true;
    line('wipe.log', `${now()} [WARN] cancel requested: finishing at the next checkpoint`);
  };

  const totalPct = 100 / plan.passes.length;
  for (const p of plan.passes) {
    if (cancelled) break;
    line('wipe', JSON.stringify({ event: 'pass_start', pass: p.n, kind: p.kind }));
    line('wipe.log', `${now()} PASS ${p.n}/${plan.passes.length}: ${p.detail}`);
    const steps = p.kind === 'sanitize' ? 12 : 20;
    for (let i = 1; i <= steps; i++) {
      if (cancelled) break;
      await sleep(p.kind === 'sanitize' ? 90 : 45);
      line('wipe', JSON.stringify({
        event: p.kind === 'sanitize' ? 'sanitize_progress' : 'pass_progress',
        passno: p.n, pct: Math.round((i / steps) * 100),
        bytes_done: Math.round((i / steps) * plan.size_bytes),
        throughput_bps: 212e6,
      }));
    }
    if (cancelled) break;
    line('wipe.log', `${now()} PASS ${p.n}/${plan.passes.length} done`);
  }

  clearTimers();
  setState({ busy: false, kind: null, started: null });
  const summary = { ok: !cancelled, cancelled, device: cfg.device,
                    passes_total: plan.passes.length,
                    passes_done: cancelled ? 'partial' : plan.passes.length,
                    duration_s: 4188 };
  line('wipe.log', `${now()} [DONE] ${cancelled ? 'cancelled' : 'wipe complete'}`);
  report('wipe.summary', summary);
  return { ok: true, error: null };
}

export async function wipeAbort() {
  if (state.kind !== 'wipe') return { ok: false, error: 'not running' };
  if (mock.abortWipe) mock.abortWipe();
  return { ok: true, error: null };
}

// -- chaff ---------------------------------------------------------------------

export function chaffPreflight(config) {
  const cfg = typeof config === 'string' ? JSON.parse(config || '{}') : (config || {});
  const target = cfg.target || '/srv/chaff';
  const amount = cfg.amount || '10 GiB';
  return { ok: true, target, amount,
           writable: true, free_bytes: 35560353792,
           reserve_bytes: 2147483648,
           estimated_files: 2100, large_warning: false,
           pack: { id: 'default', version: '1.0.0' } };
}

export async function chaffStart(config) {
  const cfg = typeof config === 'string' ? JSON.parse(config || '{}') : (config || {});
  if (state.busy) return { ok: false, error: 'busy' };
  setState({ busy: true, kind: 'chaff', started: new Date().toISOString() });

  let cancelled = false;
  mock.abortChaff = () => { cancelled = true; };

  const totalBytes = 10 * 1024 ** 3;
  let done = 0, files = 0;
  line('chaff.log', `${now()} [START] run seed=${cfg.seed || 0} profile=${cfg.profile || 'realistic-desktop'}`);
  while (done < totalBytes && !cancelled) {
    await sleep(120);
    const chunk = totalBytes / 60;
    done = Math.min(totalBytes, done + chunk);
    files += 3;
    line('chaff', JSON.stringify({
      event: 'progress', bytes_written: done, target_bytes: totalBytes,
      files, current_file: `Documents/${files} Quarterly Summary.txt`,
      free_bytes: 35560353792 - done, throughput_bps: 268e6,
    }));
  }
  setState({ busy: false, kind: null, started: null });
  const summary = { ok: !cancelled, cancelled, status: cancelled ? 'cancelled' : 'completed',
                    run_root: '/srv/chaff/Chaff_Run_20260908_101010_beef',
                    files, bytes_written: done, seed: cfg.seed || 1317,
                    manifest: '/srv/chaff/Chaff_Run_20260908_101010_beef/.chaff-manifest.json' };
  line('chaff.log', `${now()} [DONE] ${files} files, ${(done / 1024 ** 3).toFixed(1)} GiB`);
  report('chaff.summary', summary);
  return { ok: true, error: null };
}

export async function chaffCancel() {
  if (state.kind !== 'chaff') return { ok: false, error: 'not running' };
  if (mock.abortChaff) mock.abortChaff();
  return { ok: true, error: null };
}
export async function chaffPause() { line('chaff.log', `${now()} [INFO] pause requested`); return { ok: true, error: null }; }
export async function chaffResume() { line('chaff.log', `${now()} [INFO] resume`); return { ok: true, error: null }; }

// -- runs / verify / cleanup -------------------------------------------------

export async function runsList() {
  return clone(runs);
}

export async function runInspect(path) {
  const r = runs.find((x) => x.root === path) || runs[0];
  return clone({ ...r, largest: [
    { path: 'Projects/Meridian/data/payload-004.dat', size_bytes: 4294967296 },
    { path: 'Departments/Finance/2024 model.xlsx', size_bytes: 18874368 },
  ] });
}

export async function verifyStart(path) {
  line('chaff.log', `${now()} [STAGE] verifying ${path}`);
  await sleep(900);
  report('verify', { run_root: path, mode: 'full',
                     counts: { INTACT: 1284, MISSING: 0, SIZE_MISMATCH: 0,
                               HASH_MISMATCH: 0, UNREADABLE: 0 },
                     ok: true, duration_s: 2344.2 });
  return { ok: true, error: null };
}

export async function runCleanup(path, action) {
  runs = runs.filter((r) => r.root !== path);
  report('cleanup', { run_root: path, action, ok: true });
  return { ok: true, error: null };
}

// -- logs ------------------------------------------------------------------------

export async function logsList() { return clone(LOGS); }

export async function logRead(path) {
  return { path, text: `${now()} [INFO] log fixture for ${path}\n`.repeat(40) };
}

let tailTimer = null;
export async function tailStart(path) {
  tailStop();
  tailTimer = setInterval(() => {
    line('tail', `${now()} [INFO] tailing ${path}`);
  }, 2000);
}
export async function tailStop() {
  if (tailTimer) { clearInterval(tailTimer); tailTimer = null; }
}

// -- settings ----------------------------------------------------------------------

export async function settingsGet() {
  return { specs: { device: clone(SPEC_DEVICE) },
           values: { device: { allow_non_nvme: 'false' } } };
}

export async function settingsSave(values) {
  const device = (values && values.device) || values || {};
  if (device.allow_non_nvme != null) {
    const on = device.allow_non_nvme === true || device.allow_non_nvme === 'true';
    BOOTSTRAP.app_settings.allow_non_nvme = on;
  }
  return { ok: true, errors: [] };
}

// -- misc --------------------------------------------------------------------------

export async function pickDirectory() { return '/srv/chaff'; }
export async function openPath() {}
export async function revealFile() {}
export async function viewReady() {}

// hook surface the simulators use for aborts
const mock = { abortWipe: null, abortChaff: null };
