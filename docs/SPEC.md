# Chafftafarian: Specification

Chafftafarian is a Linux desktop application that combines three storage
tools in one Control Room-style interface:

1. **Chaff**: deterministic generation of realistic synthetic file corpora
   (documents, email, spreadsheets, payload blobs) into self-contained,
   verifiable run directories.
2. **Drives**: an honest inventory of attached disks: geometry, mounts,
   transport, and what the NVMe controller actually supports.
3. **Sanitize**: whole-disk sanitization for NVMe drives: the controller
   Sanitize command bracketing overwrite passes, with sampled verification,
   live per-pass progress, and cancellation that works against a root
   process.

The product lineage is deliberate: wipe a drive, then fill it with chaff.
Each half is independently usable.

> **Sanitization disclaimer.** Overwriting or filling a flash device is not
> proof of sanitization: wear-leveling, over-provisioning, and copy-on-write
> filesystems can retain original data outside the host's addressing.
> Controller-level Sanitize exists precisely because of that. This app
> reports what it did; it cannot certify what the firmware did beyond the
> sanitize status word. For policy context see NIST SP 800-88 Rev. 1
> (Clear vs Purge). Chaff generation is explicitly NOT a sanitization
> method: the app says so wherever chaff could be mistaken for one.

Status: v0.1.0, Linux-only. Architecture, UI, and packaging are adapted
from video-hls "Control Room"; chaff functionality is vendored from
chaff-generator; the wipe re-implements secure_wipe.sh v2.1.0 in Python.
See VENDORED.md for provenance.

---

## 1. Chaff

Everything in this section is the vendored chaff core
(`apps/desktop/src/chaff_generator/`); Chafftafarian adds the Control
Room-style GUI on top and changes none of its guarantees. The upstream
product spec is `major-plan.md` in the chaff-generator repository
(3,750 lines, sections 1–90); section numbers cited below were verified
against that file.

### 1.1 What a run is

One run = one directory `Chaff_Run_YYYYMMDD_HHMMSS_<4hex>/` under a
user-chosen target, containing:

- `.chaff-run.json`: the identity marker (cleanup refuses to delete
  anything without it matching),
- `.chaff-manifest.json`: per-file records (path, size, sha256, renderer,
  template, seed),
- `.chaff-journal.jsonl`: append-only write journal, fsynced every 64
  records (crash recovery evidence),
- a realistic tree (`Departments/Finance/…`, `Mail/inbox/…`,
  `Projects/<name>/…`).

Existing files in the target are never touched; files land via
`.name.chaff-partial` + atomic rename; per-file paths are checked against
symlink redirection (`safe_join`, major-plan §9.3).

### 1.2 Parameters (surface the GUI exposes)

| Parameter | Values / format | Default | Notes |
|---|---|---|---|
| target | directory path |: | holds the run directories |
| mode | `exact` / `percent_free` / `fill_until_reserve` | exact | §8; exactly one amount semantics |
| amount | size string (`10 GiB`, `1.5 GB`, fractions) | 10 GiB | exact mode; §8.1 |
| percent | 0.0–100.0 | 50 | percent_free mode; §8.2 |
| reserve | size string | 2 GB | bytes always left free; re-checked between files; §9.7 |
| profile | realistic-desktop, office-workstation, personal-computer, developer-workstation, balanced, storage-test, mixed | realistic-desktop | §69; format/content weights, size ranges, layout |
| types override | comma list of formats |: | overrides profile format mix; §21 lists the 16 formats |
| layout | flat / simple / realistic | realistic | §30 |
| seed | integer, 0 = fresh random | 0 | same seed = byte-identical corpus anywhere; §11 |
| completion | keep / delete / trash | keep | §38–41; destructive only on `completed` |
| date range | ISO dates | 2023-01-01..2026-08-01 | content timeline coherence; §64 |

Formats (§21): txt, log, md, html, csv, json, xml, eml, docx, pdf, xlsx,
pptx, vcf, ics, dat (non-sparse payload, §22), dev.

Determinism: one master seed derives per-file seeds
(`sha256("chaff-file-seed:v1:{master}:{index}")`); `.dat` payload bytes are
`shake_256` digests: streamable, deterministic, never sparse (§59).

Backstops: max 1,000,000 files per run; abort after 20 consecutive
failures; never emit a file smaller than 16 bytes; fill modes re-read the
filesystem between files ("the monitor's truth wins", §58).

### 1.3 Verification and cleanup

- Verify modes (§35–36): `metadata` (sizes), `full` (sha256 of every
  file), `sample` (deterministic subset). Verdicts per file: INTACT,
  MISSING, SIZE_MISMATCH, HASH_MISMATCH, UNREADABLE.
- Cleanup: refuses anything that is not a marker-validated run root whose
  manifest identity agrees; refuses protected roots (`/`, home, Documents,
  Downloads, Desktop) whether as target or ancestor; delete or trash.

### 1.4 What Chafftafarian adds

- The CHAFF view: form → preflight card (writability probe, free space,
  estimate, warnings) → live run (files, bytes, throughput, free space,
  pause/resume/cancel) → summary (run root, manifest path, warnings).
- The RUNS view: run history from the target directory, inspect (manifest
  summary, largest files), verify (full/sampled) with report rendering,
  cleanup with a typed `DELETE` confirmation.
- Events bridge: the core's callback events (§46) surface on the
  `chaff` channel as JSON lines; sparse human lines on `chaff.log`; the
  final `GenerationResult` as the `chaff.summary` report.

---

## 2. Drives

`lsblk -Jb -o NAME,SIZE,TYPE,KNAME,MODEL,SERIAL,TRAN,MOUNTPOINTS,FSTYPE,CHILDREN`
is the source of truth for geometry and mounts; `nvme list -o json`
enriches model/serial when nvme-cli is present. RAM disks (zram), optical
(rom), and partitions are excluded: the view lists whole disks only.

Per NVMe device the controller identify data is probed:
`nvme id-ctrl` → **sanicap**.

### Sanitize decode tables

**SANICAP** (sanitize capabilities bitfield, NVMe 2.0 bit order):

| bit | meaning |
|---|---|
| 0 | block erase supported |
| 1 | crypto erase supported |
| 2 | overwrite supported |

Caveat: NVMe 1.4 documents the crypto/overwrite bits in the opposite
order. Chafftafarian reads them per 2.0 (matching current nvme-cli
output); the hardware checklist includes verifying sanicap bits against the
vendor datasheet when crypto-erase matters.

**SANACT** (sanitize command action):

| value | action |
|---|---|
| 0 | abort a running sanitize |
| 1 | crypto erase |
| 2 | block erase: the paranoid plan's choice |
| 3 | overwrite (with pattern, via the overwrite action) |

**SSTAT** (sanitize status log, bits [2:0] = state of the most recent
sanitize):

| bits | state |
|---|---|
| 0b000 | never sanitized |
| 0b001 | completed successfully |
| 0b010 | completed unsuccessfully |
| 0b011 | in progress |

Bit 8 set means the sanitize actually modified the media (global data
erasure happened). `SSTAT = 0x101`: the value a healthy block erase
leaves behind: decodes to success + media modified. **SPROG** is a
progress fraction with denominator 65536 (`percent = sprog * 100 / 65535`).

Completion rule (matching the battle-tested bash original):
sanitize is done when the state word leaves `in-progress`, or sprog parks
at 65535. Sources: nvme-sanitize-log(1) / nvme-sanitize(1) man pages, the
NVM Express nvme-cli documentation page, and community reports; see
References.

---

## 3. Sanitize

### 3.1 The plans

**Paranoid 6-pass** (default; reproduces secure_wipe.sh v2.1.0):

| # | pass | note |
|---|---|---|
| 1 | NVMe Sanitize (block erase) | skipped, noted, when sanicap=0 |
| 2 | overwrite zeros, 1 MiB blocks | direct I/O when accepted |
| 3 | overwrite 0xFF | |
| 4 | overwrite zeros | |
| 5 | NVMe Sanitize (second) | skipped, noted, when sanicap=0 |
| 6 | overwrite zeros | leaves the drive zeroed |

**Quick**: one sanitize + one zero pass. Optional trailing **verify** pass
either way. Rationale for the bracketing: the controller erase reaches
cells the host cannot address; the overwrite passes cover drives or moments
where sanitize is unavailable and make the end state inspectable.

### 3.2 Execution model

- Writes are done by a Python pattern writer (`wipe/writer.py`), not `dd`:
  1 MiB blocks, `O_DIRECT` with a page-aligned mmap'd buffer first and a
  buffered+fdatasync fallback when the kernel refuses direct I/O, final
  fsync, ENOSPC on the last partial block reported as normal (the bash
  original's TROUBLESHOOTING documented the same). This buys exact
  progress, a cancellation checkpoint per block, and testability.
- Sanitize monitoring polls `nvme sanitize-log` only: other admin
  commands fail while a sanitize is in flight. Poll every 5 s; a sanitize
  exceeding 24 h is reported as power-cycle territory.
- Verification samples the first, middle, and last 4 KiB blocks plus a
  seeded deterministic random sample (default 64 blocks) and requires all
  to match the expected pattern. Sampling is sampling: it proves the
  sampled blocks, not the whole device.
- Cancellation: the GUI touches a `cancel` file in a 0700 control
  directory it created; the root worker checks it between write blocks,
  between passes, and on every sanitize poll. Cancel during sanitize
  issues `--sanact=0` and drains the status log (bounded to 5 minutes;
  an aborted sanitize can leave firmware opinionated).

### 3.3 Privilege model

The GUI runs unprivileged. Device operations execute in a separate root
process: the app re-executes its own binary via
`pkexec env [APPIMAGE_EXTRACT_AND_RUN=1] <exe> --wipe-worker --config <json>`,
streaming JSONL events on stdout. Notes:

- `env` is required because pkexec scrubs the environment.
- The AppImage runtime FUSE-mounts as the invoking user; a root re-exec
  would fail before AppRun runs, so the frozen form sets
  `APPIMAGE_EXTRACT_AND_RUN=1` (runtime extracts to a temp dir instead of
  mounting). Costs seconds; wipes cost hours.
- No polkit policy file is shipped: the default
  `org.freedesktop.policykit.exec` action authenticates any executable
  path. Shipping a `.policy` would pin the unstable AppImage path.
- The worker re-validates the config, re-resolves the device, and re-runs
  the safety gate: it never trusts the GUI's word. It refuses to run
  non-root. Its only inputs are the config file and the cancel file.
- The worker imports no Qt (headless root requirement); the dispatch
  happens in `__main__.py` before any Qt import.

### 3.4 Safety model

Hard refusals (no override anywhere):

- The device hosts the running OS (findmnt sources of `/ /usr /boot
  /boot/efi /etc /var` → parent disk; plus active swap from
  /proc/swaps).
- Active swap on the device.
- Not a whole disk.
- Anything mounted at the between-passes re-check (the unmount already
  happened; a mount that appears mid-wipe stops the wipe).

Soft refusals (explicitly waived by user choices that ride the config):

- Mounted partitions: refuse until the user either unmounts or checks
  "unmount for me" (the worker then umounts, logging each result: fixing
  the bash original's silent `umount … || true`).
- Non-NVMe device: gated behind a settings switch, off by default; it
  exists so the wipe path can be tested on loop devices.

Confirmation UX: to arm the wipe the user must type **DESTROY** and the
device's kernel name (`/dev/nvme1n1`), both matching, after seeing the
pass table, the mount list, and the capability verdict. One operation at
a time across the whole app (chaff or wipe, never both).

### 3.5 Fixes inherited from the redesign of secure_wipe.sh

| Original gap | Chafftafarian |
|---|---|
| No OS-disk protection (README advised Live USB only) | hard refusal via findmnt/proc-swaps |
| Silent best-effort `umount || true` | explicit choice; worker umounts with logged results; re-checks before every pass |
| No verification | optional sampled read-back pass |
| dd exit codes only warned | write failures abort the wipe |
| No protection against extra args (`DRIVE=$1`) | config-file protocol, re-validated |
| Fixed 6-pass only | paranoid / quick / verify-selected plans |

---

## 4. Architecture

Control Room pattern (from video-hls), adapted:

- **Python core** `src/chafftafarian/`: `app.py` (QApplication +
  QWebEngineView + QWebChannel), `api.py` (the single bridge object),
  `config.py` (settings + FieldSpecs), `__main__.py` (launcher, offscreen
  screenshot harness, `--wipe-worker` dispatch before Qt), `tail.py`
  (live log tail), `chaff_adapter.py` (QThread chaff worker),
  `worker_spawn.py` (pkexec launch + JSONL reader).
- **Headless wipe package** `src/chafftafarian/wipe/`: `tools.py` (the
  one subprocess seam: ToolRunner protocol, real + fake runners),
  `inventory.py`, `nvme.py` (parsers), `safety.py`, `plan.py`,
  `writer.py`, `orchestrator.py`, `protocol.py` (JSONL contract),
  `worker.py` (root entry). Qt-free.
- **Vendored chaff core** `src/chaff_generator/`: unmodified except the
  old widgets GUI is deleted. See VENDORED.md.
- **Frontend** `frontend/`: hash-routed views (01 Drives, 02 Sanitize,
  03 Chaff, 04 Runs, 05 Settings, 06 Dispatch), `?mock=1` browser mode
  with a scripted simulator, shared components and design tokens carried
  over from Control Room.
- **Bridge**: one `"api"` QObject, QString in/out, JSON payloads, slots
  never raise into JS, additive fields only. The full slot/payload
  contract is BRIDGE.md.

### The §9.5/§9.6 boundary

The chaff core's spec forbids raw block-device operations and privilege
escalation (major-plan.md §9.5, §9.6): and the vendored core keeps those
guarantees. Chafftafarian's device side violates exactly those two rules,
by design and in isolation: all block-device and privileged code lives in
`chafftafarian/wipe/` and its worker, is reached only through the
confirmation-gated SANITIZE flow, and the two modules share no state
beyond the app process. This boundary is a first-class spec invariant:
never import from `chaff_generator` inside `wipe/`, and never let `wipe/`
code run under the chaff safety contract's assumptions.

---

## 5. Packaging, testing, CI

- **Packaging**: `packaging/appimage.sh`: PyInstaller `--onedir
  --windowed`, `.desktop` (Categories=System;Filesystem), linuxdeploy,
  smoke test via `--appimage-extract-and-run --screenshot drives` plus
  `--selftest-wipe-plan` (builds a plan from bundled fixture output; no
  root, no devices).
- **Tests**: vendored chaff suites (unchanged) + bridge tests
  (`tests/test_api.py`: preflight, generation E2E, verify, cleanup
  refusal, settings) + device-layer tests (`tests/test_wipe.py`:
  parsers against fixture outputs, inventory, safety refusals, plan
  shapes, pattern writer + sampled verify on temp files, orchestrator
  fake end-to-end incl. cancel-mid-write, cancel-mid-sanitize with
  sanact=0, sanitize-failure propagation, worker config validation, and
  the headless `--wipe-worker` dispatch).
- **CI never touches real hardware**: everything device-adjacent is
  scripted through FakeToolRunner or pointed at temp files via the
  orchestrator's `dev_dir` escape hatch.

### Human-only real-device checklist

(Also in PLAN.md; run on a machine with a scratch NVMe drive.)

1. Capability probe vs `nvme id-ctrl` output and the vendor datasheet.
2. Dry-run plan on the scratch drive.
3. Cancel during pass 2: verify the write stops and the summary says
   cancelled.
4. Cancel during sanitize: verify `nvme sanitize-log` shows the abort
   path (sanact=0) and the drain.
5. Full paranoid wipe with verification.
6. Sampled verify result vs a manual spot check
   (`dd if=<dev> bs=512 count=20 | hexdump -C`).
7. pkexec prompt from the AppImage (extract-and-run path).
8. mkfs + mount the wiped drive, then fill it with chaff from EXIT 03;
   verify and clean the run.

## 6. References

- chaff-generator `major-plan.md` (sections cited inline; vendored copy
  provenance in VENDORED.md)
- `disknot-wiper-and-sanitizer/secure_wipe.sh` v2.1.0 and its README /
  TROUBLESHOOTING (pass structure, sprog/sstat completion rule, ENOSPC
  and abort guidance)
- nvme-sanitize(1), nvme-sanitize-log(1), nvme-id-ctrl(1) man pages
  (Debian manpages: manpages.debian.org/nvme-cli)
- NVM Express, "Open Source NVMe Management Utility" documentation page
  (nvmexpress.org): sanitize-log example output (SPROG 65535, SSTAT
  0x101)
- Arch Linux BBS thread on nvme sanitize SSTAT 0x101 semantics
  (bbs.archlinux.org, "nvme sanitize concerns")
- NIST SP 800-88 Rev. 1, Guidelines for Media Sanitization
- video-hls Control Room (its `apps/desktop` tree):
  bridge architecture, screenshot harness, AppImage packaging
