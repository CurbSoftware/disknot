# Vendored code

## chaff_generator

`apps/desktop/src/chaff_generator/` and `apps/desktop/tests/{unit,integration,renderers,safety}/`
are vendored from the chaff-generator repository:

- Upstream: the `disknot-chaff/chaff-generator` repository
  (public lineage: CurbSoftwareInc/Chafftafarian-Chaff-Generator)
- Vendored at commit: `5de28a0fa196aa5fc8a7ccfe7f26a88bbca56a36` (v0.1.0, MIT)
- The import name `chaff_generator` is kept unchanged: `content/bank.py` resolves the
  default pack via `importlib.resources.files("chaff_generator")` and every module and
  test imports the absolute name. Renaming would churn the whole tree for no gain.

Deletions relative to upstream (deliberate):

- `src/chaff_generator/gui/`: the old Qt-widgets GUI, replaced by the Control
  Room-style web frontend (`frontend/`) driven through `src/chafftafarian/api.py`.
- `tests/ui/`: tests for that GUI.

Additions relative to upstream:

- `apps/desktop/scripts/`: dev scripts (`benchmark.py` is invoked by
  `tests/integration/test_storage_modes.py`; the other two are reference tools).

Everything else is byte-identical to the upstream tree at the commit above
(upstream punctuation included: the no-em-dash house rule applies to this
repo's own files, not to the vendored copy). The chaff
core keeps its upstream guarantees: file-level operation only, no block devices, no
privilege escalation (major-plan.md §9.5, §9.6). Device operations live exclusively in
`src/chafftafarian/wipe/`, a separate module under separate rules.

## secure_wipe functionality

The sanitization behavior is re-implemented in Python (`src/chafftafarian/wipe/`) from
`disknot-wiper-and-sanitizer/secure_wipe.sh` v2.1.0 (MIT): the 6-pass paranoid wipe,
sanicap probing, sanitize progress via `sanitize-log` (sprog/sstat), and the abort
path. The bash script itself is not shipped; its safety gaps (silent umount, no
OS-disk refusal, no verification) are fixed rather than inherited. See docs/SPEC.md.

## Control Room patterns

The GUI architecture (QWebChannel bridge, offscreen screenshot harness, AppImage
packaging) is adapted from the video-hls Control Room app
(its `apps/desktop` tree, MIT). `frontend/js/components/`,
`frontend/css/tokens.css`, and the fonts are carried over nearly verbatim.
