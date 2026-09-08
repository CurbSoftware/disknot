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
    kname: '/dev/nvme0n1', name: 'nvme0n1', type: 'disk', transport: 'nvme',
    size_bytes: 2000398934016, model: 'Samsung SSD 990 PRO 2TB',
    serial: 'S6Z1NJ0R123456', is_nvme: true,
    sanitize: { supported: true, block_erase: true, crypto_erase: true, overwrite: false },
    children: [
      { name: 'nvme0n1p1', mountpoints: ['/boot/efi'], fstype: 'vfat', size_bytes: 536866816 },
      { name: 'nvme0n1p2', mountpoints: ['/'], fstype: 'ext4', size_bytes: 1988163260416 },
    ],
    os_disk: true, mounted: true, swap: false,
  },
  {
    kname: '/dev/nvme1n1', name: 'nvme1n1', type: 'disk', transport: 'nvme',
    size_bytes: 1000204886016, model: 'WD_BLACK SN770 1TB',
    serial: 'WDF0101ABCDEF', is_nvme: true,
    sanitize: { supported: true, block_erase: true, crypto_erase: false, overwrite: false },
    children: [
      { name: 'nvme1n1p1', mountpoints: ['/mnt/scratch'], fstype: 'ext4', size_bytes: 1000202039296 },
    ],
    os_disk: false, mounted: true, swap: false,
  },
  {
    kname: '/dev/sda', name: 'sda', type: 'disk', transport: 'usb',
    size_bytes: 30752073728, model: 'Cruzer Glide 3.0',
    serial: '030112214ABC', is_nvme: false,
    sanitize: { supported: false, block_erase: false, crypto_erase: false, overwrite: false },
    children: [],
    os_disk: false, mounted: false, swap: false,
  },
  {
    kname: '/dev/loop0', name: 'loop0', type: 'disk', transport: null,
    size_bytes: 1073741824, model: '(loop)', serial: '',
    is_nvme: false,
    sanitize: { supported: false, block_erase: false, crypto_erase: false, overwrite: false },
    children: [],
    os_disk: false, mounted: false, swap: false,
  },
];

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
    help: 'Enables sanitize/wipe on SATA, USB and loop devices. NVMe-only is the safe default; loop devices are how the wipe path is tested without spare NVMe hardware.' },
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

export async function deviceDetail(kname) {
  const d = devices.find((x) => x.kname === kname);
  if (!d) return { error: `no device ${kname}` };
  return clone({ ...d, partitions: d.children });
}

// -- wipe ----------------------------------------------------------------------

export function wipePlan(config) {
  const cfg = typeof config === 'string' ? JSON.parse(config || '{}') : (config || {});
  const dev = devices.find((x) => x.kname === cfg.device);
  if (!dev) return { error: `no device ${cfg.device}` };
  const sanitize = !!dev.sanitize.supported;
  const passes = [];
  if (sanitize) passes.push({ n: 1, kind: 'sanitize', detail: 'NVMe Sanitize (block erase)', est_s: null });
  passes.push({ n: passes.length + 1, kind: 'zeros', detail: 'Overwrite with zeros', est_s: Math.round(dev.size_bytes / 200e6) });
  passes.push({ n: passes.length + 1, kind: 'ones', detail: 'Overwrite with 0xFF', est_s: Math.round(dev.size_bytes / 200e6) });
  passes.push({ n: passes.length + 1, kind: 'zeros', detail: 'Overwrite with zeros', est_s: Math.round(dev.size_bytes / 200e6) });
  if (sanitize) passes.push({ n: passes.length + 1, kind: 'sanitize', detail: 'Second NVMe Sanitize', est_s: null });
  passes.push({ n: passes.length + 1, kind: 'zeros', detail: 'Final zero pass', est_s: Math.round(dev.size_bytes / 200e6) });
  if (cfg.verify) passes.push({ n: passes.length + 1, kind: 'verify', detail: 'Sampled read-back verification', est_s: 120 });
  const blockers = [];
  if (dev.os_disk) blockers.push('this disk hosts the running operating system');
  if (dev.mounted && !cfg.unmount) blockers.push('mounted partitions present');
  if (dev.swap) blockers.push('swap active on this disk');
  if (!dev.is_nvme && !cfg.allow_non_nvme) blockers.push('not an NVMe device (enable in settings to override)');
  return { device: dev.kname, model: dev.model, size_bytes: dev.size_bytes,
           passes, blockers, sanitize_supported: sanitize,
           confirmation: { word: 'DESTROY', device: dev.kname } };
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
  return { ok: true, errors: [] };
}

// -- misc --------------------------------------------------------------------------

export async function pickDirectory() { return '/srv/chaff'; }
export async function openPath() {}
export async function revealFile() {}
export async function viewReady() {}

// hook surface the simulators use for aborts
const mock = { abortWipe: null, abortChaff: null };
