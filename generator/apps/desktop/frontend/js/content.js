// Chafftafarian: content.js
// Every view imports from here; views never invent an explanation.
// Voice: "This drive is X." "This option does Y." Short, factual, calm.

export function fmtBytes(n) {
  if (n == null) return 'unknown size';
  let f = Number(n);
  for (const u of ['B', 'KiB', 'MiB', 'GiB', 'TiB']) {
    if (f < 1024 || u === 'TiB') {
      if (u === 'B') return `${Math.round(f)} B`;
      return `${f >= 100 ? f.toFixed(0) : f.toFixed(1)} ${u}`;
    }
    f /= 1024;
  }
  return `${n} B`;
}

export const copy = {
  drivesLede: 'Attached disks, what each one is, and what Sanitize can do to it.',
  sanitizeLede: 'This page destroys a whole disk. Pick the disk, pick the plan, type DESTROY to arm.',
  chaffLede: 'This page writes a new folder of synthetic files. It does not wipe a disk.',
  runsLede: 'This page lists chaff runs: inspect, verify, or delete them.',
  dispatchLede: 'This page lists wipe logs and chaff journals. Open one to read or tail it.',
  settingsLede: 'These settings persist. Each one changes what the app is allowed to touch.',
};

export function driveIs(dev) {
  const path = dev.path || '/dev/' + (dev.kname || '?');
  const model = dev.model || dev.kname || 'unknown model';
  return `This drive is ${path}, a ${fmtBytes(dev.size_bytes)} ${model}.`;
}

export function driveRole(dev) {
  if (dev.os_disk) return 'This drive hosts the running OS. Sanitize will always refuse it.';
  if (dev.swap) return 'This drive has active swap. Sanitize will always refuse it.';
  if (dev.mounted) return 'This drive is mounted. Sanitize will refuse it until you unmount.';
  return 'This drive is not the OS disk and is not mounted.';
}

export function sanitizeDoes(dev) {
  if (!dev.is_nvme) {
    if (dev.ata) return ataIs(dev.ata);
    return (
      'This drive is not NVMe. Inspect it for hdparm firmware erase. ' +
      'Overwrite passes need the non-NVMe setting.'
    );
  }
  const s = dev.sanitize;
  if (!s) return 'This drive could not be probed for sanitize support.';
  if (!s.supported) return 'This controller does not support Sanitize. Overwrite passes only.';
  const yes = [];
  const no = [];
  (s.block_erase ? yes : no).push('block erase');
  (s.crypto_erase ? yes : no).push('crypto erase');
  (s.overwrite ? yes : no).push('overwrite');
  let out = yes.length
    ? `This controller supports ${yes.join(', ')}.`
    : 'This controller reports Sanitize but no known method.';
  if (no.length) out += ` It does not support ${no.join(', ')}.`;
  return out;
}

export function ataIs(ata) {
  if (!ata) return 'This drive has no firmware erase via hdparm. Overwrite passes only.';
  if (ata.frozen) {
    return (
      'This drive is frozen. Firmware erase is refused. Power-cycle the drive. ' +
      'This app will not suspend the machine to thaw it. Overwrite passes still run.'
    );
  }
  const yes = [];
  if (ata.sanitize_block) yes.push('sanitize block erase');
  if (ata.sanitize_crypto) yes.push('sanitize crypto scramble');
  if (ata.security_erase_enhanced) yes.push('security erase enhanced');
  else if (ata.security_erase) yes.push('security erase');
  if (!yes.length) {
    return 'This drive has no firmware erase via hdparm. Overwrite passes only.';
  }
  return `This drive supports ${yes.join(', ')} via hdparm.`;
}

export function partitionIs(p) {
  const name = p.name || 'partition';
  const fs = p.fstype || 'unknown filesystem';
  const mps = (p.mountpoints || []).filter((m) => m && m !== '[SWAP]');
  if (mps.length) return `${name} is ${fs}, mounted at ${mps.join(', ')}.`;
  return `${name} is ${fs}, not mounted.`;
}

export function lastSanitizeIs(log) {
  if (!log) return 'Last sanitize status could not be read.';
  const byState = {
    never: 'This drive has never reported a completed sanitize.',
    success: 'Last sanitize completed successfully.',
    failed: 'Last sanitize completed unsuccessfully.',
    'in-progress': 'A sanitize is in progress on this drive. Wait for it to finish, or start a wipe: starting will abort it first (sanact=0).',
    unknown: 'Last sanitize state is unknown.',
  };
  let out = byState[log.state] || byState.unknown;
  if (log.media_modified) out += ' The controller reported that media was modified.';
  return out;
}

export function smartIs(smart, error) {
  if (error === 'missing') return 'SMART tool is not installed (smartctl).';
  if (error === 'permission') {
    return 'This machine would not let an unprivileged process read SMART on this drive.';
  }
  if (error || !smart) return 'SMART could not be read on this drive.';
  const bits = [];
  if (smart.passed === true) bits.push('health passed');
  else if (smart.passed === false) bits.push('health failed');
  else bits.push('health unknown');
  if (smart.temperature_c != null) bits.push(`${smart.temperature_c} C`);
  if (smart.power_on_hours != null) {
    bits.push(`${Number(smart.power_on_hours).toLocaleString()} hours on`);
  }
  if (smart.percentage_used != null) bits.push(`${smart.percentage_used}% wear`);
  if (smart.available_spare != null) bits.push(`${smart.available_spare}% spare`);
  if (smart.media_errors != null) bits.push(`${smart.media_errors} media errors`);
  if (smart.firmware) bits.push(`firmware ${smart.firmware}`);
  return `SMART: ${bits.join('. ')}.`;
}

export const PLAN_DOES = {
  paranoid: 'This plan runs six passes: sanitize, zeros, ones, zeros, sanitize, zeros. Longest. Most wear.',
  standard: 'This plan runs three passes: sanitize, zeros, ones. Less wear than paranoid.',
  quick: 'This plan runs sanitize, then one zero pass. Fastest.',
};

export const ACTION_DOES = {
  'block-erase': 'This option is block erase (SANACT=2). The controller erases every NAND block.',
  'crypto-erase': 'This option is crypto erase (SANACT=4). The controller destroys its media encryption key. Fast. Only if this drive reports crypto erase.',
  overwrite: 'This option is controller overwrite (SANACT=3). The controller overwrites every block with its sanitize pattern. Only if this drive reports overwrite.',
  'sata-block-erase': 'This option is ATA sanitize block erase via hdparm. The controller erases every block.',
  'sata-crypto': 'This option is ATA sanitize crypto scramble via hdparm. The controller destroys its media encryption key.',
  'sata-secure-erase': 'This option is ATA security erase via hdparm. It sets a temporary password p, then erases. If it is interrupted, the drive may stay locked.',
};

export const OPTION_DOES = {
  verify: 'This option reads a sample of blocks after the last pass. It does not prove the whole disk.',
  unmount: 'This option unmounts this drive\'s partitions, then re-checks before every pass. It will not unmount the OS disk.',
};

export function armDestroy(device) {
  return `Type DESTROY and ${device} to arm. A click is not enough.`;
}

export const whyTypedDestroy = {
  headline: 'Why typing DESTROY',
  body: 'A wipe cannot be undone. Typing DESTROY and the exact device name is the arming step. A click is not enough.',
};

export const mountedRefusal = {
  headline: 'Why mounted drives are refused',
  body: 'Writing over a mounted filesystem corrupts it while the OS is still using it. Unmount first, or check unmount for me. The OS disk is never unmounted.',
};

export const seedDeterminism = {
  headline: 'This seed makes the corpus reproducible',
  body: 'The same seed, pack, and settings write the same bytes on any machine. Seed 0 draws a fresh random seed.',
};

export const reserveMeaning = {
  headline: 'This reserve is free space that must remain',
  body: 'The engine re-reads free space between files and stops before free space drops below the reserve.',
};

export const chaffDisclaimer = {
  headline: 'Chaff is not sanitization',
  body: 'Filling a filesystem is not a wipe. Use Sanitize for destruction. See NIST SP 800-88 Rev. 1 on clearing vs purging.',
};

export const MODE_DOES = {
  exact: 'This mode writes a fixed size.',
  percent_free: 'This mode writes a percent of current free space.',
  fill_until_reserve: 'This mode fills until only the reserve is left free.',
};

export const PROFILE_DOES = {
  'realistic-desktop': 'This profile writes everyday documents, mail, and sheets.',
  'office-workstation': 'This profile writes memos, spreadsheets, invoices, and mail.',
  'personal-computer': 'This profile writes notes, letters, and casual mail.',
  'developer-workstation': 'This profile writes projects, logs, data files, and specs.',
  balanced: 'This profile writes a mix of every supported format.',
  'storage-test': 'This profile writes normal files plus large storage payloads.',
  mixed: 'This profile writes a blend of personal, business, and technical files.',
};

export const LAYOUT_DOES = {
  realistic: 'This layout builds a department / mail / projects tree.',
  simple: 'This layout uses a few top-level folders.',
  flat: 'This layout puts every file in one directory.',
};

export const COMPLETION_DOES = {
  keep: 'This option keeps the run directory when generation finishes.',
  delete: 'This option deletes the run directory when generation finishes.',
  trash: 'This option moves the run directory to trash when generation finishes.',
};

export function afterWipeIs(summary, plan) {
  if (!summary) return '';
  if (summary.cancelled) {
    return 'The wipe was cancelled. The drive is not a finished wipe.';
  }
  if (!summary.ok) {
    return 'The wipe ended with problems. Do not assume the drive is clean.';
  }
  const writes = (plan && plan.passes ? plan.passes : []).filter(
    (p) => p.kind === 'zeros' || p.kind === 'ones',
  );
  const last = writes[writes.length - 1];
  if (last && last.kind === 'zeros') {
    return 'This drive is left zeroed. Format it (mkfs), mount it, then open EXIT 03 Chaff to fill it.';
  }
  return 'The wipe finished. Format the drive (mkfs), mount it, then open EXIT 03 Chaff to fill it.';
}

export const troubleshooting = [
  { happen: 'The password dialog never appears.',
    do: 'pkexec needs a polkit agent on this desktop session.' },
  { happen: 'The plan skips Sanitize.',
    do: 'This controller reported no sanitize support. Overwrite passes still run. Remapped cells may retain data.' },
  { happen: 'Sanitize looks stuck.',
    do: 'The controller is working. Large drives take tens of minutes. Cancel issues nvme sanitize --sanact=0. Current nvme-cli lists 0 as reserved; a wedged drive may need a power cycle.' },
  { happen: 'ATA sanitize is running and you hit Cancel.',
    do: 'ATA sanitize cannot be aborted from the host. This app waits for the controller, then skips remaining passes.' },
  { happen: 'The SATA drive is frozen.',
    do: 'Power-cycle the drive. This app will not suspend the machine to thaw it. Overwrite passes still run.' },
  { happen: 'A write pass ends with no space left.',
    do: 'That is normal on the last partial block of a whole-disk write. It is not a failure.' },
  { happen: 'Verify found non-matching blocks.',
    do: 'The sample did not match the expected pattern. Remapped sectors can do that when Sanitize was skipped.' },
];
