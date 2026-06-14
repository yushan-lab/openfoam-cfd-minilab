#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p "$ROOT_DIR/results/logs" "$ROOT_DIR/figures" "$ROOT_DIR/results"

echo "BEGIN_OPENFOAM_RUN"
pwd
ls -la
echo "Container user:"
id
ls -ld "$ROOT_DIR" "$ROOT_DIR/results" "$ROOT_DIR/results/logs" "$ROOT_DIR/cases" "$ROOT_DIR/figures"

if ! touch "$ROOT_DIR/results/logs/write_test.log"; then
    echo "Container cannot write to results/logs; check Docker --user or workspace permissions." >&2
    exit 1
fi
rm -f "$ROOT_DIR/results/logs/write_test.log"

SOURCE_LOG="$ROOT_DIR/results/logs/source_openfoam.log"
set +e
if [ -f /opt/openfoam11/etc/bashrc ]; then
    echo "Sourcing /opt/openfoam11/etc/bashrc" | tee "$SOURCE_LOG"
    source /opt/openfoam11/etc/bashrc >> "$SOURCE_LOG" 2>&1
    SOURCE_STATUS=$?
elif [ -f /usr/lib/openfoam/openfoam11/etc/bashrc ]; then
    echo "Sourcing /usr/lib/openfoam/openfoam11/etc/bashrc" | tee "$SOURCE_LOG"
    source /usr/lib/openfoam/openfoam11/etc/bashrc >> "$SOURCE_LOG" 2>&1
    SOURCE_STATUS=$?
else
    echo "Could not locate OpenFOAM 11 bashrc." | tee "$SOURCE_LOG"
    SOURCE_STATUS=1
fi
set -e

echo "OpenFOAM source status: $SOURCE_STATUS"
cat "$SOURCE_LOG"

echo "WM_PROJECT_DIR=${WM_PROJECT_DIR:-unset}"
echo "PATH=$PATH"
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-unset}"

command -v blockMesh || true
command -v checkMesh || true
command -v icoFoam || true
command -v postProcess || true

if ! command -v blockMesh >/dev/null 2>&1 \
    || ! command -v checkMesh >/dev/null 2>&1 \
    || ! command -v icoFoam >/dev/null 2>&1; then
    find /opt /usr \( -name blockMesh -o -name icoFoam \) 2>/dev/null | sort || true
    echo "OpenFOAM commands are not available after sourcing bashrc." >&2
    exit 1
fi

set +e
MESH_RESOLUTION=40 RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh
RUN_STATUS=$?
set -e

echo "run_cavity.sh exit status: $RUN_STATUS"

for f in \
    results/logs/blockMesh.log \
    results/logs/checkMesh.log \
    results/logs/icoFoam.log \
    results/logs/writeCellCentres.log \
    results/logs/writeCellCentres_skipped.log \
    results/logs/foamToVTK.log \
    results/logs/foamToVTK_skipped.log \
    results/logs/python_postprocess_skipped.log; do
    if [ -f "$f" ]; then
        echo "===== tail: $f ====="
        tail -n 80 "$f" || true
    else
        echo "Missing log: $f"
    fi
done

find results figures -maxdepth 4 -type f | sort

if [ "$RUN_STATUS" -ne 0 ]; then
    echo "run_cavity.sh failed; see log tails above." >&2
    exit "$RUN_STATUS"
fi

for log_file in \
    results/logs/blockMesh.log \
    results/logs/checkMesh.log \
    results/logs/icoFoam.log; do
    if [ ! -s "$log_file" ]; then
        echo "Required solver log is missing or empty: $log_file" >&2
        exit 1
    fi
done

if ! grep -q "End" results/logs/icoFoam.log; then
    echo "icoFoam.log does not contain the solver completion marker: End" >&2
    exit 1
fi

find results figures -maxdepth 4 -type f | sort
echo "END_OPENFOAM_RUN"
