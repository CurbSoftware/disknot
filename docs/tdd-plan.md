# Disknot TDD plan

This is the test plan for Disknot: drive detection, the chaff generator cards, wipe and sanitize, root versus a normal user, the pkexec restart, and the versioned AppImage name `disknot-0.1.0-x86_64.AppImage`.

The suite that locks this plan already lives in `generator/apps/desktop/tests/`. Pytest is the only test runner. The frontend cases start Node from pytest and assert in pytest. There is no second framework.

## How to run

From `generator/apps/desktop`:

```bash
uv sync --extra dev
uv run pytest
```

The new files alone:

```bash
uv run pytest \
  tests/test_suite_drives.py \
  tests/test_suite_chaff.py \
  tests/test_suite_wipe.py \
  tests/test_suite_privilege.py \
  tests/test_suite_appimage.py \
  tests/test_suite_backend.py \
  tests/test_suite_frontend.py
```

`tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` before Qt imports. API tests use `QCoreApplication`, not a window. Frontend tests use Node's process and a small DOM stand-in (`tests/frontend_harness.mjs`). They skip when `node` is missing. The published-folder check skips when `/home/user/projects/published/disknot` is absent, so CI still runs.

Nothing in this suite opens a real block device, calls the real `pkexec`, or builds an AppImage.

Older files stay in force: `tests/test_wipe.py`, `tests/test_api.py`, `tests/test_relaunch.py`, and the vendored chaff suites under `tests/unit`, `tests/integration`, `tests/renderers`, and `tests/safety`. A change is green only when `uv run pytest` is green, including those.

## The cycle

1. Write the assertion in the file named below. Run that file. It should fail for the reason you expect.
2. Change the smallest piece of product code that makes that assertion pass.
3. Run the file again, then `uv run pytest`.
4. Leave the test in place. The next change starts from red again.

Fake `lsblk`, `nvme`, `findmnt`, and `hdparm` answers live in `tests/suite_fakes.py` (`ScriptedRunner`). Device code already takes a `ToolRunner`. Tests pass that runner. They do not shell out to the real tools except for two tool-seam cases: a missing binary, and a `sleep` that hits the timeout path in `SubprocessToolRunner`.

## Drives

`inventory.scan_devices` is the logic. `Api.deviceList` is the bridge. `frontend/js/views/drives.js` is the page.

A good scan returns whole disks, partition mount points, and swap. zram, ram disks, optical drives, and partition nodes are dropped. A missing size is 0. Tests can point `dev_dir` at a temp directory so paths are not `/dev/...`.

| Behavior | Test |
| --- | --- |
| Two disks, path prefix rewritten | `test_scan_lists_whole_disks_and_rewrites_dev_dir` |
| Partitions, nested mounts, swap flag | `test_scan_keeps_partitions_mounts_and_swap` |
| Swap on the whole disk | `test_whole_disk_swap_without_partitions` |
| zram, ram, sr, and parts dropped | `test_scan_drops_zram_ram_and_optical` |
| Junk children ignored | `test_scan_skips_non_dicts_and_bad_children` |
| Null size becomes 0 | `test_scan_treats_missing_size_as_zero` |
| lsblk argv has real columns, not `CHILDREN` | `test_lsblk_argv_does_not_ask_for_children_column` |
| nvme list fills a blank model and leaves an lsblk model | `test_enrich_fills_blank_model_and_leaves_lsblk_model` |
| Failed nvme list leaves the disk as lsblk saw it | `test_enrich_ignores_failed_nvme_list` |
| Bridge returns disks, NVMe sanitize bits, and `os_disk: false` | `test_device_list_returns_disks` |
| Inspect reads sanitize-log | `test_device_detail_includes_sanitize_log` |
| Unknown path | `test_device_detail_missing` |
| Page lists the model, path, and `1 DISK` | `test_drives_render_disks` |
| OS disk has no Sanitize button | `test_drives_os_disk_has_no_sanitize_button` |
| Missing tools chip | `test_drives_missing_tools_chip` |
| Inspect card shows SMART and last sanitize | `test_drives_inspect` |

`list_devices` drops the error string on purpose. Callers that need the reason use `scan_devices`. `test_list_devices_hides_the_error_string` locks that split. `Api.deviceList` uses `scan_devices` and returns `{devices, error}`.

Copy for the drive sentences is `frontend/js/content.js`. `test_copy_strings` checks `driveIs`, `driveRole`, `sanitizeDoes`, `partitionIs`, and `fmtBytes`.

## Chaff cards

The chaff page is three cards, in this order:

1. **Job** (`01`). Target, amount mode, reserve, profile, layout, seed, types, completion. Preflight and Generate sit on this card. Preflight's tip says it writes nothing.
2. **Preflight** (`02`). Starts as `NOT RUN YET`. A refusal is `REFUSED` plus the error string. A clear probe is `WHAT THE ENGINE SEES` and `CLEAR`. Warnings flip the chip to `NOTES`.
3. **Ground rules** (`03`). Seed, reserve, and the line that chaff is not sanitization.

| Behavior | Test |
| --- | --- |
| The three card titles and the ground-rule headlines | `test_chaff_cards_job_preflight_ground_rules` |
| Mode copy, profiles, layouts, completions | `test_copy_strings` |
| Switching amount mode to percent of free | `test_chaff_amount_mode_switches` |
| Refused preflight text on the card | `test_chaff_preflight_refused` |
| Clear preflight rows | `test_chaff_preflight_clear` |
| Warning chip | `test_chaff_preflight_notes` |
| Generate with an empty target runs preflight and does not call `chaffStart` | `test_chaff_start_without_target_runs_preflight` |
| Writable directory, probe file removed | `test_preflight_on_a_writable_directory`, `test_api_preflight_ok` |

The engine checks the target with `check_writable_dir` before it plans files. A missing directory, a file path, a directory the user cannot write (`chmod 555`), and an `OSError` that says the filesystem is read-only all raise `ChaffError` with a plain sentence. Those are `test_preflight_refuses_a_missing_directory`, `test_preflight_refuses_a_file`, `test_preflight_refuses_a_read_only_directory`, and `test_preflight_reports_a_read_only_filesystem`.

The bridge slot catches that exception today and returns `{"ok": false, "error": "internal error"}`. The directory is left untouched. `test_api_preflight_read_only_is_not_ok`, `test_api_preflight_missing_directory_is_not_ok`, and `test_api_preflight_file_target_is_not_ok` lock that.

Next red test, when we want the card to show the engine sentence through the real bridge: change `test_api_preflight_read_only_is_not_ok` so `error` contains `Permission denied`, watch it fail, then catch `ChaffError` in `Api._chaff_preflight` and return that message. The Preflight card already prints `out.error`. `test_chaff_preflight_refused` covers the card with a stubbed bridge.

## Wipe and sanitize

Plans are built before anything is written.

| Plan | Passes with sanitize support |
| --- | --- |
| paranoid | sanitize, zeros, ones, zeros, sanitize, zeros |
| standard | sanitize, zeros, ones |
| quick | sanitize, zeros |

`test_plan_shapes` locks those three. `test_verify_is_a_trailing_pass` adds verify at the end. `test_missing_sanitize_drops_those_passes` keeps the overwrite passes and records that sanitize was skipped. `test_crypto_request_falls_back_to_block_erase` falls back when the controller has no crypto erase. `test_frozen_sata_keeps_overwrite_passes` refuses firmware erase on a frozen SATA drive and still keeps the zero pass.

Safety, from `safety.check_device`:

| Case | Kind | Test |
| --- | --- | --- |
| OS disk | hard, no waiver | `test_os_disk_is_a_hard_block` |
| Active swap | hard | `test_swap_is_a_hard_block` |
| Mounted filesystem | soft, waived by unmount | `test_mounted_is_soft_until_unmount_is_planned` |
| Non-NVMe | soft, waived by the setting | `test_non_nvme_is_soft_until_allowed` |
| A partition, not a whole disk | hard | `test_partition_is_not_a_disk` |
| A mount that appears between passes | hard | `test_recheck_promotes_mounts_to_hard` |

`test_parent_disk_names` covers `nvme0n1p2` to `nvme0n1`, `sda3` to `sda`, and `vda`.

The bridge:

| Behavior | Test |
| --- | --- |
| Quick plan, `DESTROY` confirmation, no blockers | `test_wipe_plan_for_a_listed_disk` |
| Unknown device | `test_wipe_plan_unknown_device` |
| Mounted disk lists a blocker and still returns the passes | `test_wipe_plan_lists_mount_blockers` |
| Wrong confirmation does not spawn a worker | `test_wipe_start_refuses_confirmation_mismatch` |
| Blockers do not spawn a worker | `test_wipe_start_refuses_blockers_without_spawning` |
| Unknown device on start | `test_wipe_start_unknown_device` |

The page:

| Behavior | Test |
| --- | --- |
| Target card, plan card, `CLEAR TO CONFIRM` | `test_sanitize_plan_card` |
| `BLOCKED` plus the mount text | `test_sanitize_blocked` |
| lsblk error replaces the form | `test_sanitize_lsblk_error` |
| A normal user clicking destroy asks for the password and does not call `wipeStart` | `test_sanitize_non_root_asks_for_pkexec` |

`test_copy_strings` also locks `PLAN_DOES`, `armDestroy`, and the after-wipe sentences.

The root worker re-checks uid before it touches a device. With uid 1000 it emits `hello`, then `error` containing `not running as root` and `pkexec`, then `done` with `ok: false`, and exits 1. That is `test_worker_refuses_non_root`. It monkeypatches `os.getuid`, so the test is safe even if the process is root.

Bad worker config exits 2 and writes to stderr: `test_worker_bad_config_exits_2`, `test_worker_rejects_a_non_object_config`, `test_worker_rejects_an_unreadable_config`. Torn JSONL lines are skipped by `test_read_events_skips_torn_lines`.

Do not add a test that calls `wipeStart` with a matching `DESTROY` and a clear plan. That path spawns `pkexec`.

## Root and pkexec

A normal session shows the privilege banner: wipe needs root, disk listing still runs, button label `Restart with privileges`. Root hides the banner. A failed restart leaves the banner up and shows the error. The button is enabled again after the failure.

| Behavior | Test |
| --- | --- |
| Bootstrap `root` matches euid, tools include `pkexec` and `lsblk` | `test_bootstrap_reports_root` |
| Banner copy when not root | `test_privilege_banner_when_not_root` |
| Banner hidden when root | `test_privilege_banner_hidden_when_root` |
| polkit failure stays on the banner | `test_privilege_pkexec_failure_stays_visible` |
| Already root returns `already_root` and does not spawn | `test_relaunch_when_already_root` |
| pkexec stderr is the error | `test_relaunch_failure_stays_on_screen` |
| Empty failure becomes `pkexec exited 1 with no message` | `test_relaunch_empty_pkexec_failure` |
| `OSError` from spawn | `test_relaunch_oserror` |
| Success with `/bin/true` | `test_relaunch_success_does_not_require_a_window` |

The restart command must use the real AppImage file, not the FUSE mount under `/tmp/.mount_*` or any path containing `/.mount_`. The script sets `APPIMAGE_EXTRACT_AND_RUN=1` and `setsid`, and it does not pass `-m` when the image file exists.

| Behavior | Test |
| --- | --- |
| Versioned file wins over the mount path | `test_versioned_image_beats_the_fuse_mount` |
| Relative `ARGV0` resolves from `OWD` | `test_relative_argv0_resolves_from_owd` |
| Mount path alone is rejected | `test_mount_only_path_is_rejected` |
| Missing file is rejected | `test_missing_image_file_is_rejected` |
| pkexec script quotes `disknot-0.1.0-x86_64.AppImage` | `test_relaunch_argv_is_pkexec_and_extract_and_run` |
| No image: `pkexec` plus `python -m chafftafarian` | `test_relaunch_argv_falls_back_to_the_module` |
| Frozen binary outside the mount is the target | `test_frozen_binary_outside_the_mount` |
| Frozen binary inside the mount is not used | `test_frozen_binary_inside_the_mount_is_not_used` |
| Worker argv is `pkexec` plus `--wipe-worker` | `test_worker_argv_is_pkexec` |
| `APPIMAGE` set adds `APPIMAGE_EXTRACT_AND_RUN=1` on the worker | `test_worker_argv_sets_extract_and_run_inside_an_appimage` |
| Control directory mode is `0700` | `test_control_dir_is_private` |

## AppImage name

Version `0.1.0` in `generator/apps/desktop/pyproject.toml` and `chafftafarian.__version__` produce this filename:

`disknot-0.1.0-x86_64.AppImage`

| Source | What it must contain | Test |
| --- | --- | --- |
| `packaging/appimage.sh` | `APPIMAGE_FILE="disknot-${APP_VERSION}-x86_64.AppImage"` | `test_appimage_script_uses_the_versioned_name` |
| `package.sh` | `disknot-<version>-x86_64.AppImage` | `test_package_script_points_at_the_versioned_name` |
| `scripts/publish.sh` | copy to `disknot-${version}-x86_64.AppImage` | `test_publish_script_copies_the_versioned_name` |
| CI and release workflows | `packaging/disknot-*-x86_64.AppImage` | `test_release_workflow_globs_the_versioned_name` |
| Python version and the joined filename | `disknot-0.1.0-x86_64.AppImage` | `test_version_is_0_1_0` |
| Packaging does not assign the old bare names | no `disknot-x86_64.AppImage` or `chafftafarian-x86_64.AppImage` as `APPIMAGE_FILE` | `test_unversioned_name_is_not_what_packaging_writes` |

On a machine that has `/home/user/projects/published/disknot`, `test_published_folder_has_the_versioned_appimage` checks that the only AppImage file in that folder is `disknot-0.1.0-x86_64.AppImage`. The test reads the name. It does not read the binary into git.

## Failure paths

### lsblk

`scan_devices` never raises for a bad lsblk result. The device list is empty and `error` says why.

| lsblk result | Error string | Test |
| --- | --- | --- |
| stderr `permission denied`, exit 1 | that stderr | `test_lsblk_error_text_is_kept` |
| stderr `command not found`, exit 127 | that stderr | same, second case |
| empty stderr, exit 2 | `lsblk exited 2` | same, third case |
| stdout is not JSON | `lsblk did not return JSON.` | `test_lsblk_non_json` |
| `{}` or a JSON array | `lsblk JSON had no blockdevices list.` | `test_lsblk_without_blockdevices` |
| `blockdevices` is an object | same | `test_lsblk_blockdevices_not_a_list` |
| empty device list | `lsblk reported no whole disks.` | `test_lsblk_with_no_whole_disks` |

The bridge returns the same strings: `test_device_list_surfaces_lsblk_errors`, `test_device_list_surfaces_bad_json`, `test_device_list_when_lsblk_exits_quietly`, `test_device_list_when_lsblk_reports_no_disks`.

The drives page shows `Could not list disks`, the error text, and `NO DISKS SEEN`: `test_drives_lsblk_error`. An empty list with a null error becomes `Disk scan returned nothing.`: `test_drives_empty_scan`. Sanitize shows the same headline and does not ask for a plan: `test_sanitize_lsblk_error`.

A missing binary is exit 127 from `SubprocessToolRunner`: `test_missing_binary_is_a_tool_error`. A timeout is exit 124: `test_tool_timeout_is_reported`.

### Read-only paths

| Path | Result | Test |
| --- | --- | --- |
| Chaff target the user cannot write | `ChaffError` with `Permission denied`, no probe file left behind | `test_preflight_refuses_a_read_only_directory` |
| Read-only filesystem `OSError` | `Cannot write` | `test_preflight_reports_a_read_only_filesystem` |
| Same target through the API | `ok: false`, directory unchanged | `test_api_preflight_read_only_is_not_ok` |
| AppImage path inside the FUSE mount | rejected, relaunch uses the real file | `test_versioned_image_beats_the_fuse_mount`, `test_mount_only_path_is_rejected` |
| Worker config path that cannot be read | exit 2 | `test_worker_rejects_an_unreadable_config` |

The FUSE mount is read-only for a root re-exec. That is why the restart refuses `/.mount_` paths and sets `APPIMAGE_EXTRACT_AND_RUN=1`.

## Where each layer lives

| Layer | What it covers | Files |
| --- | --- | --- |
| Logic | parsers, plans, safety, worker uid, AppImage path, writability | `test_suite_drives.py`, `test_suite_wipe.py`, `test_suite_chaff.py`, `test_suite_privilege.py`, `test_suite_appimage.py` |
| Backend | `Api` slots with a fake runner | `test_suite_backend.py`, the `api` tests in `test_suite_chaff.py` and `test_suite_privilege.py` |
| Frontend | card HTML, chips, clicks | `test_suite_frontend.py` plus `frontend_harness.mjs`, `frontend_loader.mjs`, `frontend_stub_api.mjs` |
| UI copy | sentences the views are allowed to show | `test_copy_strings` importing `content.js` |

`frontend_loader.mjs` redirects `api.js` to the stub so the views render without Qt WebChannel. Pytest checks the HTML the views produce.

## Still a person, on a spare disk

These stay out of pytest. `PLAN.md` in `generator/` already lists the real-device checklist.

- A real `lsblk` on this machine's disks.
- A real polkit password dialog.
- A sanitize or overwrite against hardware.
- Building and smoking the AppImage (`./package.sh`). The suite checks the filename the scripts would write. It does not run linuxdeploy.

When one of those is done by hand, write down the disk, the plan name, and whether the banner, the preflight card, or the AppImage name matched this document.

## Adding a case

Pick the layer. Logic first, then the bridge, then the view.

Example, a new lsblk failure: add a row to `test_lsblk_error_text_is_kept` or a new test next to `test_lsblk_non_json`. Run `uv run pytest tests/test_suite_drives.py::test_lsblk_non_json`. When the string is wrong, fix `scan_devices`, then add the same string to `test_device_list_surfaces_bad_json` and `test_drives_lsblk_error` if the page should show it.

Example, the preflight sentence on the real bridge: edit `test_api_preflight_read_only_is_not_ok` to expect `Permission denied`. Run it red. Catch `ChaffError` in `_chaff_preflight`. Run the chaff file, then the full suite.

Keep new tests in the `test_suite_*.py` files. Do not add a JS test runner. Do not point tests at `/dev/nvme*` or `/dev/sd*` on the host.
