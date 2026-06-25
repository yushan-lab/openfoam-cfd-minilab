#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASE_DIR="$ROOT_DIR/cases/lid_driven_cavity"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR}"
LOG_DIR="$ROOT_DIR/results/logs"
FIGURE_DIR="$ROOT_DIR/figures"
RESULT_DIR="$ROOT_DIR/results"
MESH_RESOLUTION="${MESH_RESOLUTION:-40}"
MESH_DICT="$CASE_DIR/system/blockMeshDict.${MESH_RESOLUTION}x${MESH_RESOLUTION}"
RUN_PYTHON_POSTPROCESS="${RUN_PYTHON_POSTPROCESS:-1}"
END_TIME="${END_TIME:-5}"
DELTA_T="${DELTA_T:-0.0025}"
COURANT_LIMIT="${COURANT_LIMIT:-0.5}"
WRITE_INTERVAL_TIME="${WRITE_INTERVAL_TIME:-0.5}"
OVERWRITE="${OVERWRITE:-0}"
START_FROM_LATEST="${START_FROM_LATEST:-0}"
STEADY_THRESHOLD="${STEADY_THRESHOLD:-1e-5}"
PURGE_WRITE="${PURGE_WRITE:-3}"
SAVE_FINAL_FIELDS="${SAVE_FINAL_FIELDS:-auto}"
SAVE_FINAL_MINUS_INTERVAL="${SAVE_FINAL_MINUS_INTERVAL:-0}"
RUN_DIR_ISOLATED=0

if [ "$OUTPUT_ROOT" != "$ROOT_DIR" ]; then
    RUN_DIR_ISOLATED=1
    LOG_DIR="$OUTPUT_ROOT/logs"
    RESULT_DIR="$OUTPUT_ROOT/results"
    FIGURE_DIR="$OUTPUT_ROOT/figures"
    METADATA_DIR="$OUTPUT_ROOT/metadata"
    if [ -d "$OUTPUT_ROOT" ] && [ -n "$(find "$OUTPUT_ROOT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        if [ "$OVERWRITE" != "1" ]; then
            echo "Refusing to overwrite non-empty OUTPUT_ROOT=$OUTPUT_ROOT. Set OVERWRITE=1 to replace it." >&2
            exit 3
        fi
        if [ "$START_FROM_LATEST" != "1" ]; then
            rm -rf "$OUTPUT_ROOT"
        fi
    fi
else
    METADATA_DIR="$RESULT_DIR/metadata"
fi

if [ "$SAVE_FINAL_FIELDS" = "auto" ]; then
    SAVE_FINAL_FIELDS="$RUN_DIR_ISOLATED"
fi

mkdir -p "$LOG_DIR" "$FIGURE_DIR" "$RESULT_DIR" "$METADATA_DIR"
ICOFOAM_LOG="$LOG_DIR/icoFoam.log"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

WRITE_INTERVAL_STEPS="$("$PYTHON_BIN" - "$DELTA_T" "$WRITE_INTERVAL_TIME" <<'PY'
import math
import sys

delta_t = float(sys.argv[1])
write_interval_time = float(sys.argv[2])
if delta_t <= 0:
    raise SystemExit("DELTA_T must be positive.")
steps = write_interval_time / delta_t
rounded = int(round(steps))
if rounded <= 0 or not math.isclose(rounded * delta_t, write_interval_time, rel_tol=0, abs_tol=1e-12):
    raise SystemExit(
        f"WRITE_INTERVAL_TIME={write_interval_time} must be an integer multiple of DELTA_T={delta_t}."
    )
print(rounded)
PY
)"

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
    if [ "$MESH_RESOLUTION" != "80" ] && [ "$MESH_RESOLUTION" != "160" ]; then
        echo "Unsupported MESH_RESOLUTION=$MESH_RESOLUTION. Expected 20, 40, 80, or 160." >&2
        exit 2
    fi
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

if [ -f "$MESH_DICT" ]; then
    cp "$MESH_DICT" "$CASE_DIR/system/blockMeshDict"
else
    "$PYTHON_BIN" scripts/generate_cavity_mesh.py --resolution "$MESH_RESOLUTION" --output "$CASE_DIR/system/blockMeshDict"
fi

START_FROM_SETTING="startTime"
if [ "$START_FROM_LATEST" = "1" ]; then
    START_FROM_SETTING="latestTime"
    rm -rf "$CASE_DIR/postProcessing" "$CASE_DIR/VTK"
else
    find "$CASE_DIR" -maxdepth 1 -type d \
        \( -name "[1-9]*" -o -name "0.*" \) \
        -exec rm -rf {} +
    rm -rf "$CASE_DIR/constant/polyMesh" "$CASE_DIR/postProcessing" "$CASE_DIR/VTK"
fi

CONTROL_BACKUP="$(mktemp)"
cp "$CASE_DIR/system/controlDict" "$CONTROL_BACKUP"
restore_control_dict() {
    cp "$CONTROL_BACKUP" "$CASE_DIR/system/controlDict"
    rm -f "$CONTROL_BACKUP"
}
trap 'restore_control_dict; print_file_summary' EXIT

cat > "$CASE_DIR/system/controlDict" <<EOF_CONTROL
/*--------------------------------*- C++ -*----------------------------------*\\
| OpenFOAM: Transient icoFoam controls                                       |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}

application     icoFoam;

startFrom       $START_FROM_SETTING;
startTime       0;
stopAt          endTime;
endTime         $END_TIME;

deltaT          $DELTA_T;
adjustTimeStep  no;

writeControl    timeStep;
writeInterval   $WRITE_INTERVAL_STEPS;
purgeWrite      $PURGE_WRITE;

writeFormat     ascii;
writePrecision  6;
writeCompression off;

timeFormat      general;
timePrecision   6;

runTimeModifiable true;
EOF_CONTROL

START_EPOCH="$(date +%s)"
START_TIME_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
GIT_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
OPENFOAM_VERSION="${WM_PROJECT_VERSION:-unknown}"

{
    echo "Executed from: $CASE_DIR"
    echo "Selected mesh: ${MESH_RESOLUTION}x${MESH_RESOLUTION}"
    echo "RUN_PYTHON_POSTPROCESS=$RUN_PYTHON_POSTPROCESS"
    echo "OUTPUT_ROOT=$OUTPUT_ROOT"
    echo "END_TIME=$END_TIME"
    echo "DELTA_T=$DELTA_T"
    echo "COURANT_LIMIT=$COURANT_LIMIT"
    echo "WRITE_INTERVAL_TIME=$WRITE_INTERVAL_TIME"
    echo "WRITE_INTERVAL_STEPS=$WRITE_INTERVAL_STEPS"
    echo "START_FROM_LATEST=$START_FROM_LATEST"
    echo "STEADY_THRESHOLD=$STEADY_THRESHOLD"
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
icoFoam -case "$CASE_DIR" > "$ICOFOAM_LOG" 2>&1
verify_solver_logs

if [ "$SAVE_FINAL_FIELDS" = "1" ]; then
    FINAL_FIELD_DIR="$OUTPUT_ROOT/final_fields"
    LATEST_TIME_DIR="$(
        find "$CASE_DIR" -maxdepth 1 -type d \( -name '[1-9]*' -o -name '0.*' \) \
            -printf '%f\n' | sort -g | tail -n 1
    )"
    if [ -z "$LATEST_TIME_DIR" ]; then
        echo "Could not find final OpenFOAM time directory for final field copy." >&2
        exit 4
    fi
    mkdir -p "$FINAL_FIELD_DIR"
    cp "$CASE_DIR/$LATEST_TIME_DIR/U" "$FINAL_FIELD_DIR/U"
    cp "$CASE_DIR/$LATEST_TIME_DIR/p" "$FINAL_FIELD_DIR/p"
    if [ "$SAVE_FINAL_MINUS_INTERVAL" != "0" ]; then
        FINAL_MINUS_TIME_DIR="$(
            "$PYTHON_BIN" - "$CASE_DIR" "$LATEST_TIME_DIR" "$SAVE_FINAL_MINUS_INTERVAL" <<'PY'
import pathlib
import sys

case_dir = pathlib.Path(sys.argv[1])
latest_time = float(sys.argv[2])
interval = float(sys.argv[3])
target = latest_time - interval
times = []
for path in case_dir.iterdir():
    if not path.is_dir():
        continue
    try:
        times.append((float(path.name), path.name))
    except ValueError:
        pass
if not times:
    raise SystemExit(1)
print(min(times, key=lambda item: abs(item[0] - target))[1])
PY
        )"
        FINAL_MINUS_DIR="$OUTPUT_ROOT/final_minus_${SAVE_FINAL_MINUS_INTERVAL}_fields"
        mkdir -p "$FINAL_MINUS_DIR"
        cp "$CASE_DIR/$FINAL_MINUS_TIME_DIR/U" "$FINAL_MINUS_DIR/U"
    fi
fi

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
    END_EPOCH="$(date +%s)"
    END_TIME_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    WALL_CLOCK_SECONDS="$((END_EPOCH - START_EPOCH))"
    export METADATA_DIR MESH_RESOLUTION END_TIME DELTA_T COURANT_LIMIT WALL_CLOCK_SECONDS GIT_COMMIT OPENFOAM_VERSION START_TIME_UTC END_TIME_UTC OUTPUT_ROOT ICOFOAM_LOG
    "$PYTHON_BIN" -c 'import json, os, pathlib
pathlib.Path(os.environ["METADATA_DIR"]).mkdir(parents=True, exist_ok=True)
icofoam_log = pathlib.Path(os.environ["ICOFOAM_LOG"])
time_values = []
max_co_values = []
if icofoam_log.exists():
    for line in icofoam_log.read_text().splitlines():
        if line.startswith("Time = "):
            try:
                time_values.append(float(line.split("=", 1)[1].strip().rstrip("s")))
            except ValueError:
                pass
        if "Courant Number mean:" in line and " max:" in line:
            try:
                max_co_values.append(float(line.rsplit(" max:", 1)[1].strip()))
            except ValueError:
                pass
deltas = [b - a for a, b in zip(time_values, time_values[1:])]
courant_limit = float(os.environ["COURANT_LIMIT"])
observed_max_co = max(max_co_values) if max_co_values else None
payload = {
    "resolution": int(os.environ["MESH_RESOLUTION"]),
    "status": "completed",
    "cell_count": int(os.environ["MESH_RESOLUTION"]) ** 2,
    "dx": 1.0 / int(os.environ["MESH_RESOLUTION"]),
    "final_time": float(os.environ["END_TIME"]),
    "relative_L2_change": None,
    "requested_deltaT": float(os.environ["DELTA_T"]),
    "observed_deltaT_min": min(deltas) if deltas else None,
    "observed_deltaT_max": max(deltas) if deltas else None,
    "observed_maxCo": observed_max_co,
    "courant_limit": courant_limit,
    "courant_gate_passed": None if observed_max_co is None else observed_max_co <= courant_limit,
    "number_of_steps": len(time_values),
    "wall_clock_seconds": float(os.environ["WALL_CLOCK_SECONDS"]),
    "git_commit": os.environ["GIT_COMMIT"],
    "openfoam_version": os.environ["OPENFOAM_VERSION"],
    "start_time_utc": os.environ["START_TIME_UTC"],
    "end_time_utc": os.environ["END_TIME_UTC"],
    "exact_command": "MESH_RESOLUTION={0} OUTPUT_ROOT={1} END_TIME={2} DELTA_T={3} bash scripts/run_cavity.sh".format(os.environ["MESH_RESOLUTION"], os.environ["OUTPUT_ROOT"], os.environ["END_TIME"], os.environ["DELTA_T"]),
}
(pathlib.Path(os.environ["METADATA_DIR"]) / "run_metadata.json").write_text(json.dumps(payload, indent=2) + "\n")
'
    exit 0
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" scripts/plot_residuals.py \
    --log "$LOG_DIR/icoFoam.log" \
    --output "$FIGURE_DIR/cavity_residuals.png" \
    --csv "$RESULT_DIR/residuals.csv" \
    --continuity-csv "$RESULT_DIR/continuity_errors.csv" \
    --summary-json "$RESULT_DIR/solver_summary.json"

"$PYTHON_BIN" scripts/postprocess_cavity.py \
    --case cases/lid_driven_cavity \
    --results "$RESULT_DIR" \
    --figures "$FIGURE_DIR"

if [ "$(find "$CASE_DIR" -maxdepth 1 -type d \( -name '[1-9]*' -o -name '0.*' \) | wc -l)" -ge 2 ]; then
    "$PYTHON_BIN" scripts/check_cavity_steady_state.py \
        --case "$CASE_DIR" \
        --threshold "$STEADY_THRESHOLD" \
        --output "$METADATA_DIR/steady_state.json" || true
fi

END_EPOCH="$(date +%s)"
END_TIME_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
WALL_CLOCK_SECONDS="$((END_EPOCH - START_EPOCH))"
export METADATA_DIR MESH_RESOLUTION END_TIME DELTA_T COURANT_LIMIT WALL_CLOCK_SECONDS GIT_COMMIT OPENFOAM_VERSION START_TIME_UTC END_TIME_UTC OUTPUT_ROOT ICOFOAM_LOG
"$PYTHON_BIN" -c 'import json, os, pathlib
metadata_dir = pathlib.Path(os.environ["METADATA_DIR"])
metadata_dir.mkdir(parents=True, exist_ok=True)
steady_path = metadata_dir / "steady_state.json"
steady = json.loads(steady_path.read_text()) if steady_path.exists() else {}
icofoam_log = pathlib.Path(os.environ["ICOFOAM_LOG"])
time_values = []
max_co_values = []
if icofoam_log.exists():
    for line in icofoam_log.read_text().splitlines():
        if line.startswith("Time = "):
            try:
                time_values.append(float(line.split("=", 1)[1].strip().rstrip("s")))
            except ValueError:
                pass
        if "Courant Number mean:" in line and " max:" in line:
            try:
                max_co_values.append(float(line.rsplit(" max:", 1)[1].strip()))
            except ValueError:
                pass
deltas = [b - a for a, b in zip(time_values, time_values[1:])]
courant_limit = float(os.environ["COURANT_LIMIT"])
observed_max_co = max(max_co_values) if max_co_values else None
payload = {
    "resolution": int(os.environ["MESH_RESOLUTION"]),
    "status": "completed",
    "cell_count": int(os.environ["MESH_RESOLUTION"]) ** 2,
    "dx": 1.0 / int(os.environ["MESH_RESOLUTION"]),
    "final_time": float(os.environ["END_TIME"]),
    "converged": steady.get("converged"),
    "relative_L2_change": steady.get("relative_L2_change"),
    "requested_deltaT": float(os.environ["DELTA_T"]),
    "observed_deltaT_min": min(deltas) if deltas else None,
    "observed_deltaT_max": max(deltas) if deltas else None,
    "observed_maxCo": observed_max_co,
    "courant_limit": courant_limit,
    "courant_gate_passed": None if observed_max_co is None else observed_max_co <= courant_limit,
    "number_of_steps": len(time_values),
    "wall_clock_seconds": float(os.environ["WALL_CLOCK_SECONDS"]),
    "git_commit": os.environ["GIT_COMMIT"],
    "openfoam_version": os.environ["OPENFOAM_VERSION"],
    "start_time_utc": os.environ["START_TIME_UTC"],
    "end_time_utc": os.environ["END_TIME_UTC"],
    "exact_command": "MESH_RESOLUTION={0} OUTPUT_ROOT={1} END_TIME={2} DELTA_T={3} bash scripts/run_cavity.sh".format(os.environ["MESH_RESOLUTION"], os.environ["OUTPUT_ROOT"], os.environ["END_TIME"], os.environ["DELTA_T"]),
}
(metadata_dir / "run_metadata.json").write_text(json.dumps(payload, indent=2) + "\n")
'
