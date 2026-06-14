#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p "$ROOT_DIR/results/logs" "$ROOT_DIR/figures" "$ROOT_DIR/results"

echo "BEGIN_OPENFOAM_RUN"
pwd
ls -la

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

MESH_RESOLUTION=40 RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh

for log_file in \
    results/logs/blockMesh.log \
    results/logs/checkMesh.log \
    results/logs/icoFoam.log; do
    if [ ! -s "$log_file" ]; then
        echo "Required solver log is missing or empty: $log_file" >&2
        exit 1
    fi
done

tail -n 30 results/logs/blockMesh.log
tail -n 30 results/logs/checkMesh.log
tail -n 30 results/logs/icoFoam.log

find results figures -maxdepth 4 -type f | sort
echo "END_OPENFOAM_RUN"

