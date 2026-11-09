#!/usr/bin/env bash
set -euo pipefail
archive=${1:?Pass the generated or staged libidevice_ffi.a}
output=${2:?Pass a symbol-evidence output path}
# Reuse the exact pinned-component admission checks; never pick nm from PATH.
python3 -B - "$archive" "$output" "$(dirname "$0")" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import verify_native_abi as gate
nm, provenance = gate.resolve_pinned_nm()
command, output, diagnostics = gate.inspect_symbols(nm, Path(sys.argv[1]), gate.required_symbols())
Path(sys.argv[2]).write_text(output)
if diagnostics:
    print(diagnostics, file=sys.stderr, end='')
PY
