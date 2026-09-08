# BRIDGE.md — the Qt ↔ HTML contract (frozen)

Chafftafarian embeds the frontend in a `QWebEngineView`. Python exposes
exactly one object over a `QWebChannel`: id **`api`**. Every slot takes and
returns **strings** (`QString`); every payload is **JSON** encoded in
UTF-8. Async slots return immediately (JS gets `undefined`); results
stream back as signals.

Do not change payload field names. Additive fields may appear — ignore ones
you do not know.

## Signals (Qt → JS)

### `lineReady(QString channel, QString text)`

| channel      | source                                          |
|--------------|-------------------------------------------------|
| `chaff`      | one engine event as JSON (see below)            |
| `chaff.log`  | sparse human-readable generation lines          |
| `wipe`       | one worker event as JSON (see below)            |
| `wipe.log`   | formatted human wipe lines                      |
| `tail`       | live tail started via `tailStart(path)`         |

### `reportReady(QString channel, QString json)`

| channel          | payload                                             |
|------------------|-----------------------------------------------------|
| `chaff.summary`  | final GenerationResult + `ok`, `cancelled`          |
| `verify`         | verification report (counts, affected, ok)          |
| `cleanup`        | runCleanup result                                   |
| `probe`          | reserved: device/tool probe summary                 |

### `stateChanged(QString json)`

`{"busy": bool, "kind": "chaff"|"wipe"|null, "started": iso|null,
"core_version": str, "tools": {name: path|null}}` — one operation at a
time across the whole app.

## Slots

### `QString getBootstrap()`

```json
{"core": {"version": "0.1.0", "ready": true},
 "tools": {"nvme": "/usr/sbin/nvme", "pkexec": "/usr/bin/pkexec",
           "lsblk": "/usr/bin/lsblk", "findmnt": "/usr/bin/findmnt"},
 "busy": false, "kind": null,
 "app_settings": {"allow_non_nvme": false, "default_target": ""}}
```

`tools` values are `null` when missing. `default_target` is where runs
live (set after the first successful generation).

### Devices

- `QString deviceList()` → array of devices:

```json
{"kname": "nvme1n1", "path": "/dev/nvme1n1", "type": "disk",
 "transport": "nvme", "size_bytes": 1000204886016,
 "model": "WD_BLACK SN770 1TB", "serial": "…",
 "is_nvme": true, "os_disk": false, "mounted": false, "swap": false,
 "mountpoints": [], "partitions": [{"name": "nvme1n1p1",
 "size_bytes": 1, "mountpoints": [], "fstype": "ext4"}],
 "sanitize": {"supported": true, "block_erase": true,
              "crypto_erase": false, "overwrite": false}}
```

`sanitize` is `null` for non-NVMe or a failed probe.

- `QString deviceDetail(pathOrKname)` → one device or `{"error": …}`.

### Wipe

- `QString wipePlan(config_json)` — config
  `{"device", "verify"?, "quick"?, "unmount"?}` → plan:

```json
{"device": "/dev/nvme1n1", "model": "…", "size_bytes": …,
 "passes": [{"n": 1, "kind": "sanitize|zeros|ones|verify",
             "detail": "…", "est_s": 5001|null}],
 "sanitize_supported": true, "blockers": ["…"], "notes": ["…"],
 "confirmation": {"word": "DESTROY", "device": "/dev/nvme1n1"}}
```

`blockers` is empty when the plan may proceed.

- `QString wipeStart(config_json)` — config adds `confirm_word`
  (must be `"DESTROY"`) and `confirm_device` (must equal `device`).
  Sync ack `{"ok": bool, "error": str|null}`; events then stream on the
  `wipe`/`wipe.log` channels; a final `wipe.summary` report.

- `QString wipeAbort()` → `{"ok": bool, "error": …}`; cancellation lands
  at the next worker checkpoint (write block, pass boundary, or sanitize
  poll — during sanitize the worker issues `--sanact=0`).

Worker events on the `wipe` channel (one JSON per line):

```json
{"event": "hello", "pid": 1234, "device": "…", "uid": 0}
{"event": "log"|"warning"|"error", "text": "…"}
{"event": "pass_start", "passno": 2, "kind": "zeros", "detail": "…"}
{"event": "pass_progress", "passno": 2, "bytes_done": …, "bytes_total": …, "percent": 41.2}
{"event": "sanitize_progress", "passno": 1, "percent": 63.0, "sstat": 3, "state": "in-progress"}
{"event": "verify_progress", "passno": 7, "checked": 12, "of": 67}
{"event": "done", "ok": true, "cancelled": false, "passes_total": 6,
 "passes_done": [1,2,3,4,5,6], "verify_ok": true, "verify_mismatches": [],
 "warnings": [], "errors": [], "duration_s": 4188.2}
```

(The field is `passno`, not `pass`: it must survive Python keyword rules.)

### Chaff

- `QString chaffPreflight(config_json)` → sync, cheap:
  `{"ok": true, "target_path": …, "free_bytes": …, "requested_bytes": …,
  "reserve_bytes": …, "estimated_file_count": …, "formats": […],
  "warnings": […]}`.
- `QString chaffStart(config_json)` → ack; events stream on `chaff`
  (engine events, each carrying `"event": "ProgressUpdated"` etc. plus the
  dataclass fields); human lines on `chaff.log`; final `chaff.summary`
  carrying the `GenerationResult` fields plus `ok`/`cancelled`.
- `chaffCancel/chaffPause/chaffResume()` → `{"ok": bool, "error": …}`.

Config shape (the core's `dict_to_config` validates; see SPEC §1.2):

```json
{"target": {"path": "/srv/chaff", "mode": "exact|percent_free|fill_until_reserve",
            "amount": "10 GiB", "percent": "50", "reserve": "2 GB"},
 "profile": "realistic-desktop", "seed": 0,
 "directory_layout": "realistic", "completion": "keep",
 "file_types": {"txt": true}}
```

### Runs

- `QString runsList(path="")` → array of
  `{root, run_id, created_at, app_version, has_manifest, file_count,
  bytes_written, status}` (empty list when no default target is set).
- `QString runInspect(path)` → manifest summary + five largest files, or
  `{"error": …}` for run dirs without a manifest.
- `QString verifyStart(path, mode="full|sample|metadata")` → ack; one
  `verify` report: counts by verdict, `affected` (first 50 non-INTACT),
  `files_checked/total`, bytes, `ok`, `cancelled`.
- `QString runCleanup(path, action="delete|trash")` → `{"ok", "warnings"}`
  or a refusal error (the vendored safety checklist applies).

### Settings

- `QString settingsGet()` →
  `{"specs": {"device": [FieldSpec…]}, "values": {"device": {name: value}}}`
  where FieldSpec is `{name, default, kind, choices, help, label, group}`.
- `QString settingsSave(values_json)` → `{"ok": bool, "errors": [...]}`.

### Logs / OS

- `QString logsList()` → wipe archives then chaff journals:
  `{engine, name, path, size_bytes, mtime}`.
- `QString logRead(path, tail_bytes="65536")` → `{path, text}` or
  `{path, text: "", error: "not found"}`.
- `void tailStart(path)` / `void tailStop()` — lines arrive on `tail`.
- `QString pickDirectory(current)` → chosen path or "".
- `void openPath(path)` / `void revealFile(path)`.
- `void viewReady(view)` — the screenshot harness waits on this.
