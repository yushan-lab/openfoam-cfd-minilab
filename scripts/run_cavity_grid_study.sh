#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ROOT="${RUN_ROOT:-$ROOT_DIR/runs/cavity_validation}"
RESOLUTIONS="${RESOLUTIONS:-20 40 80 160}"
END_TIME="${END_TIME:-20}"
MAX_END_TIME="${MAX_END_TIME:-50}"
EXTEND_TIME="${EXTEND_TIME:-10}"
DELTA_T="${DELTA_T:-0.0025}"
COURANT_LIMIT="${COURANT_LIMIT:-0.5}"
STEADY_THRESHOLD="${STEADY_THRESHOLD:-1e-5}"
OVERWRITE="${OVERWRITE:-0}"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi
MANIFEST="$RUN_ROOT/manifest.csv"

mkdir -p "$RUN_ROOT"
echo "resolution,status,output_root" > "$MANIFEST"

read_converged_status() {
    local output_root="$1"
    "$PYTHON_BIN" - "$output_root" <<'PY'
import json
import pathlib
import sys

steady_path = pathlib.Path(sys.argv[1]) / "metadata" / "steady_state.json"
if not steady_path.exists():
    print("unknown")
    raise SystemExit(0)

steady = json.loads(steady_path.read_text())
print("true" if steady.get("converged") is True else "false")
PY
}

mark_not_converged() {
    local output_root="$1"
    "$PYTHON_BIN" - "$output_root" <<'PY'
import json
import pathlib
import sys

metadata_path = pathlib.Path(sys.argv[1]) / "metadata" / "run_metadata.json"
metadata_path.parent.mkdir(parents=True, exist_ok=True)
metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
metadata["status"] = "not_converged"
metadata["converged"] = False
metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
PY
}

update_grid_metadata_totals() {
    local output_root="$1"
    local wall_clock_seconds="$2"
    local number_of_steps="$3"
    "$PYTHON_BIN" - "$output_root" "$wall_clock_seconds" "$number_of_steps" <<'PY'
import json
import pathlib
import sys

metadata_path = pathlib.Path(sys.argv[1]) / "metadata" / "run_metadata.json"
metadata = json.loads(metadata_path.read_text())
metadata["wall_clock_seconds"] = float(sys.argv[2])
metadata["number_of_steps"] = int(sys.argv[3])
metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
PY
}

check_run_outputs() {
    local OUTPUT_ROOT="$1"
    test -s "$OUTPUT_ROOT/logs/blockMesh.log"
    test -s "$OUTPUT_ROOT/logs/checkMesh.log"
    test -s "$OUTPUT_ROOT/logs/icoFoam.log"
    test -s "$OUTPUT_ROOT/results/residuals.csv"
    test -s "$OUTPUT_ROOT/results/continuity_errors.csv"
    test -s "$OUTPUT_ROOT/results/solver_summary.json"
    test -s "$OUTPUT_ROOT/results/centerline_u.csv"
    test -s "$OUTPUT_ROOT/results/centerline_v.csv"
    test -s "$OUTPUT_ROOT/figures/cavity_residuals.png"
    test -s "$OUTPUT_ROOT/figures/cavity_centerline_profiles.png"
}

for resolution in $RESOLUTIONS; do
    OUTPUT_ROOT="$RUN_ROOT/N$resolution"
    target_end_time="$END_TIME"
    attempt=0
    grid_start_epoch="$(date +%s)"
    total_number_of_steps=0

    while true; do
        set +e
        if [ "$attempt" -eq 0 ]; then
            MESH_RESOLUTION="$resolution" \
                OUTPUT_ROOT="$OUTPUT_ROOT" \
                END_TIME="$target_end_time" \
                DELTA_T="$DELTA_T" \
                COURANT_LIMIT="$COURANT_LIMIT" \
                STEADY_THRESHOLD="$STEADY_THRESHOLD" \
                OVERWRITE="$OVERWRITE" \
                bash "$ROOT_DIR/scripts/run_cavity.sh"
        else
            MESH_RESOLUTION="$resolution" \
                OUTPUT_ROOT="$OUTPUT_ROOT" \
                END_TIME="$target_end_time" \
                DELTA_T="$DELTA_T" \
                COURANT_LIMIT="$COURANT_LIMIT" \
                STEADY_THRESHOLD="$STEADY_THRESHOLD" \
                START_FROM_LATEST=1 \
                OVERWRITE=1 \
                bash "$ROOT_DIR/scripts/run_cavity.sh"
        fi
        status=$?
        set -e

        if [ "$status" -ne 0 ]; then
            echo "$resolution,failed,$OUTPUT_ROOT" >> "$MANIFEST"
            break
        fi

        if ! check_run_outputs "$OUTPUT_ROOT"; then
            echo "$resolution,failed,$OUTPUT_ROOT" >> "$MANIFEST"
            break
        fi

        segment_steps="$(
            "$PYTHON_BIN" - "$OUTPUT_ROOT" <<'PY'
import json
import pathlib
import sys

metadata_path = pathlib.Path(sys.argv[1]) / "metadata" / "run_metadata.json"
metadata = json.loads(metadata_path.read_text())
print(metadata.get("number_of_steps") or 0)
PY
        )"
        total_number_of_steps="$((total_number_of_steps + segment_steps))"

        converged_status="$(read_converged_status "$OUTPUT_ROOT")"
        if [ "$converged_status" = "true" ]; then
            update_grid_metadata_totals \
                "$OUTPUT_ROOT" \
                "$(( $(date +%s) - grid_start_epoch ))" \
                "$total_number_of_steps"
            echo "$resolution,completed,$OUTPUT_ROOT" >> "$MANIFEST"
            break
        fi

        if [ "$target_end_time" -ge "$MAX_END_TIME" ]; then
            mark_not_converged "$OUTPUT_ROOT"
            update_grid_metadata_totals \
                "$OUTPUT_ROOT" \
                "$(( $(date +%s) - grid_start_epoch ))" \
                "$total_number_of_steps"
            echo "$resolution,not_converged,$OUTPUT_ROOT" >> "$MANIFEST"
            break
        fi

        target_end_time="$((target_end_time + EXTEND_TIME))"
        if [ "$target_end_time" -gt "$MAX_END_TIME" ]; then
            target_end_time="$MAX_END_TIME"
        fi
        attempt="$((attempt + 1))"
    done
done

"$PYTHON_BIN" "$ROOT_DIR/scripts/evaluate_cavity_validation.py" \
    --runs-root "$RUN_ROOT" \
    --reference-dir "$ROOT_DIR/data/reference" \
    --output-dir "$ROOT_DIR/results/public" \
    --figures-dir "$ROOT_DIR/figures" \
    --allow-missing-reference
