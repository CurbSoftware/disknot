"""PyInstaller entry point for the frozen app.

PyInstaller needs a plain script; src/chafftafarian/__main__.py can't be
one (its relative imports only resolve under `python -m chafftafarian`,
which is exactly how dev runs it). This launcher reproduces that: it
imports the package's __main__ and hands it the real argv: including the
internal `--wipe-worker` path pkexec re-executes as root.
"""

import sys

from chafftafarian.__main__ import main

sys.exit(main(sys.argv[1:]))
