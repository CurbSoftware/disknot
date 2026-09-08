// Chafftafarian — content.js
// Education and safety copy. Every view imports from here; views never
// restate an explanation. Keep strings terse, factual, and calm — the user
// is about to do something irreversible, and panic copy helps nobody.

export const sanitizeVsOverwrite = {
  headline: 'Controller sanitize vs overwrite passes',
  body:
    'Flash storage remaps blocks behind your back: a plain overwrite cannot ' +
    'reach retired or over-provisioned cells. The NVMe Sanitize command makes ' +
    'the controller itself erase every block, including those. That is why ' +
    'the paranoid plan brackets the overwrite passes with two sanitize ' +
    'operations: sanitize does what the OS cannot, overwrites cover drives ' +
    'or moments where sanitize is unavailable.',
};

export const whyTypedDestroy = {
  headline: 'Why typing DESTROY',
  body:
    'A wipe is irreversible and unverified-able after the fact. The typed ' +
    'confirmation plus the device name is a two-factor farewell: your hand ' +
    'must produce both the word and the exact kernel name of the drive you ' +
    'meant. A mis-click cannot satisfy it.',
};

export const mountedRefusal = {
  headline: 'Why mounted drives are refused',
  body:
    'Writing a pattern over a mounted filesystem corrupts it while the OS is ' +
    'still using it. The app refuses any drive with mounted partitions until ' +
    'they are unmounted, re-checks immediately before every pass, and never ' +
    'offers to unmount the disk your operating system is running from.',
};

export const seedDeterminism = {
  headline: 'Seeds make chaff reproducible',
  body:
    'One master seed derives a per-file seed for every generated file. The ' +
    'same seed, the same pack version, and the same settings reproduce a ' +
    'byte-identical corpus on any machine — which is what makes a chaff run ' +
    'verifiable after the fact. Seed 0 draws a fresh random seed for you.',
};

export const reserveMeaning = {
  headline: 'The reserve is headroom, not a suggestion',
  body:
    'The engine re-reads free space between files and stops before free ' +
    'space drops below the reserve. It trusts the filesystem, not its own ' +
    'byte counter — so an external program consuming space mid-run is ' +
    'accounted for, not overwritten into an ENOSPC.',
};

export const chaffDisclaimer = {
  headline: 'Chaff is not sanitization',
  body:
    'Generating files until a filesystem is full pushes the previous ' +
    'deleted data out of reach of simple undelete tools, but it is NOT a ' +
    'sanitization method: flash over-provisioning, wear-leveling and ' +
    'copy-on-write filesystems can all retain the originals. For ' +
    'sanitization use the SANITIZE view. For context see NIST SP 800-88 ' +
    'Rev. 1 on clearing vs purging.',
};

export const passTable = [
  { pass: 1, kind: 'sanitize', detail: 'NVMe Sanitize (block erase) — controller-level' },
  { pass: 2, kind: 'zeros', detail: 'Overwrite with zeros, 1 MiB blocks, direct' },
  { pass: 3, kind: 'ones', detail: 'Overwrite with 0xFF' },
  { pass: 4, kind: 'zeros', detail: 'Overwrite with zeros again' },
  { pass: 5, kind: 'sanitize', detail: 'Second NVMe Sanitize' },
  { pass: 6, kind: 'zeros', detail: 'Final zero pass — leaves the drive zeroed' },
];

// Troubleshooting map — keyed by the symptom a user sees.
export const troubleshooting = [
  { symptom: 'pkexec dialog never appears',
    fix: 'pkexec needs a polkit agent on your desktop session. Check that polkit-gnome or the equivalent is running.' },
  { symptom: 'Drive does not support Sanitize',
    fix: 'sanicap=0 in the controller identify data. The plan degrades to overwrite passes automatically; the two sanitize passes are skipped with a note.' },
  { symptom: 'Sanitize seems stuck',
    fix: 'Sanitize runs entirely in the controller and blocks most other admin commands while it works. Large drives take tens of minutes. To abort: nvme sanitize <dev> --sanact=0. A drive that stays wedged may need a power cycle.' },
  { symptom: 'Write pass ends with "no space left"',
    fix: 'ENOSPC on the final block of a whole-disk write is normal — the last partial block cannot fit. It is reported, not treated as failure.' },
  { symptom: 'Verification found non-matching blocks',
    fix: 'The verify sample reads back blocks after the final pass. Mismatches after a zero pass on a drive that skipped sanitize can indicate remapped sectors — run the paranoid plan with sanitize enabled.' },
];

// Shared micro-copy
export const copy = {
  drivesLede: 'The drive bay. What is attached, what it can survive, and what it is doing right now.',
  sanitizeLede: 'Irreversible, by design. Plan it, confirm it, watch every pass.',
  chaffLede: 'Realistic synthetic files, deterministically generated and verifiably intact.',
  runsLede: 'Every chaff run on record: manifests, verification, and cleanup.',
  dispatchLede: 'Every departure is logged. Tails run live; archives stay on file.',
};
