#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASE_DIR="$ROOT_DIR/cases/lid_driven_cavity"
LOG_DIR="$ROOT_DIR/results/logs"
FIGURE_DIR="$ROOT_DIR/figures"
RESULT_DIR="$ROOT_DIR/results"
MESH_RESOLUTION="${MESH_RESOLUTION:-40}"
MESH_DICT="$CASE_DIR/system/blockMeshDict.${MESH_RESOLUTION}x${MESH_RESOLUTION}"
RUN_PYTHON_POSTPROCESS="${RUN_PYTHON_POSTPROCESS:-1}"

mkdir -p "$LOG_DIR" "$FIGURE_DIR" "$RESULT_DIR"

print_file_summary() {
    echo ""
    echo "Result and figure file summary:"
    find "$ROOT_DIR/results" "$ROOT_DIR/figures" -maxdepth 3 -type f | sort
}

verify_nonempty_file() {
    local path="$1"
    if [ ! -s "$path" ]; then
        echo "Required solver log is missing or empty: $path" >&2
        return 1
    fi
}

verify_solver_logs() {
    verify_nonempty_file "$LOG_DIR/blockMesh.log"
    verify_nonempty_file "$LOG_DIR/checkMesh.log"
    verify_nonempty_file "$LOG_DIR/icoFoam.log"
}

trap print_file_summary EXIT

if [ ! -f "$MESH_DICT" ]; then
    echo "Unsupported MESH_RESOLUTION=$MESH_RESOLUTION. Expected 20 or 40." >&2
    exit 2
fi

missing=()
for cmd in blockMesh checkMesh icoFoam; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        missing+=("$cmd")
    fi
done

if [ "${#missing[@]}" -gt 0 ]; then
    {
        echo "OpenFOAM command(s) not found on PATH: ${missing[*]}"
        echo "No mesh, solver, residual, or figure output was generated."
        echo "Requested mesh dictionary: $MESH_DICT"
        echo ""
        echo "Run this from an OpenFOAM-enabled shell:"
        echo "  cd \"$ROOT_DIR\""
        echo "  MESH_RESOLUTION=$MESH_RESOLUTION bash scripts/run_cavity.sh"
        echo "or select the other bundled mesh with MESH_RESOLUTION=20 or 40."
    } | tee "$LOG_DIR/openfoam_unavailable.log"
    exit 127
fi

cp "$MESH_DICT" "$CASE_DIR/system/blockMeshDict"
{
    echo "Executed from: $CASE_DIR"
    echo "Selected mesh: ${MESH_RESOLUTION}x${MESH_RESOLUTION}"
    echo "RUN_PYTHON_POSTPROCESS=$RUN_PYTHON_POSTPROCESS"
    echo "cp system/blockMeshDict.${MESH_RESOLUTION}x${MESH_RESOLUTION} system/blockMeshDict"
    echo "blockMesh -case \"$CASE_DIR\""
    echo "checkMesh -case \"$CASE_DIR\""
    echo "icoFoam -case \"$CASE_DIR\""
    if command -v postProcess >/dev/null 2>&1; then
        echo "postProcess -case \"$CASE_DIR\" -func writeCellCentres -latestTime  # optional"
    fi
    if command -v foamToVTK >/dev/null 2>&1; then
        echo "foamToVTK -case \"$CASE_DIR\" -latestTime -fields \"(U)\"  # optional"
    fi
    echo "python scripts/plot_residuals.py --log results/logs/icoFoam.log --output figures/cavity_residuals.png --csv results/residuals.csv"
    echo "python scripts/postprocess_cavity.py --case cases/lid_driven_cavity --results results --figures figures"
} > "$LOG_DIR/command_sequence.txt"

blockMesh -case "$CASE_DIR" > "$LOG_DIR/blockMesh.log" 2>&1
checkMesh -case "$CASE_DIR" > "$LOG_DIR/checkMesh.log" 2>&1
icoFoam -case "$CASE_DIR" > "$LOG_DIR/icoFoam.log" 2>&1
verify_solver_logs

if command -v postProcess >/dev/null 2>&1; then
    if ! postProcess -case "$CASE_DIR" -func writeCellCentres -latestTime > "$LOG_DIR/writeCellCentres.log" 2>&1; then
        {
            echo "postProcess writeCellCentres failed; continuing because blockMesh, checkMesh, and icoFoam succeeded."
            echo "Python post-processing will reconstruct structured cell centres from system/blockMeshDict if C is unavailable."
            echo "See $LOG_DIR/writeCellCentres.log for the OpenFOAM diagnostic output."
        } > "$LOG_DIR/writeCellCentres_skipped.log"
    fi
else
    {
        echo "postProcess not found; skipping optional writeCellCentres."
        echo "Python post-processing will reconstruct structured cell centres from system/blockMeshDict."
    } > "$LOG_DIR/writeCellCentres_skipped.log"
fi

if command -v foamToVTK >/dev/null 2>&1; then
    if ! foamToVTK -case "$CASE_DIR" -latestTime -fields "(U)" > "$LOG_DIR/foamToVTK.log" 2>&1; then
        {
            echo "foamToVTK failed; skipping optional VTK field export."
            echo "See $LOG_DIR/foamToVTK.log for the OpenFOAM diagnostic output."
        } > "$LOG_DIR/foamToVTK_skipped.log"
    fi
else
    echo "foamToVTK not found; skipping optional VTK field export." > "$LOG_DIR/foamToVTK_skipped.log"
fi

verify_solver_logs

if [ "$RUN_PYTHON_POSTPROCESS" = "0" ]; then
    echo "Skipping Python post-processing because RUN_PYTHON_POSTPROCESS=0." > "$LOG_DIR/python_postprocess_skipped.log"
    exit 0
fi

PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" scripts/plot_residuals.py \
    --log results/logs/icoFoam.log \
    --output figures/cavity_residuals.png \
    --csv results/residuals.csv

"$PYTHON_BIN" scripts/postprocess_cavity.py \
    --case cases/lid_driven_cavity \
    --results results \
    --figures figures
