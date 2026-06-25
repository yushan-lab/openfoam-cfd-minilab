#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
MODEL="${MODEL:-kEpsilon}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/runs/rans_pitzdaily_smoke/$MODEL}"
MAX_ITERATIONS="${MAX_ITERATIONS:-50}"
OVERWRITE="${OVERWRITE:-0}"

if [ -f /opt/openfoam10/etc/bashrc ] && [ "${WM_PROJECT_VERSION:-}" != "10" ]; then
    # shellcheck disable=SC1091
    source /opt/openfoam10/etc/bashrc
fi

command -v blockMesh >/dev/null 2>&1 || { echo "Required OpenFOAM command not found: blockMesh" >&2; exit 1; }
command -v checkMesh >/dev/null 2>&1 || { echo "Required OpenFOAM command not found: checkMesh" >&2; exit 1; }
command -v potentialFoam >/dev/null 2>&1 || { echo "Required OpenFOAM command not found: potentialFoam" >&2; exit 1; }
command -v simpleFoam >/dev/null 2>&1 || { echo "Required OpenFOAM command not found: simpleFoam" >&2; exit 1; }
command -v postProcess >/dev/null 2>&1 || { echo "Required OpenFOAM command not found: postProcess" >&2; exit 1; }

case "$MODEL" in
    kEpsilon|kOmegaSST) ;;
    *)
        echo "MODEL must be kEpsilon or kOmegaSST, got: $MODEL" >&2
        exit 1
        ;;
esac

PREPARE_ARGS=(
    "$ROOT_DIR/scripts/prepare_rans_pitzdaily_case.py"
    --model "$MODEL"
    --output "$OUTPUT_ROOT"
    --max-iterations "$MAX_ITERATIONS"
)
if [ "$OVERWRITE" = "1" ]; then
    PREPARE_ARGS+=(--overwrite)
fi

"$PYTHON_BIN" "${PREPARE_ARGS[@]}"

PAIR_ROOT="$(dirname "$OUTPUT_ROOT")"
if [ -f "$PAIR_ROOT/kEpsilon/case_manifest.json" ] && [ -f "$PAIR_ROOT/kOmegaSST/case_manifest.json" ]; then
    "$PYTHON_BIN" "$ROOT_DIR/scripts/audit_rans_case_pair.py" \
        --k-epsilon "$PAIR_ROOT/kEpsilon" \
        --k-omega-sst "$PAIR_ROOT/kOmegaSST" \
        --output "$PAIR_ROOT/pair_audit.json" >/dev/null
fi

LOG_DIR="$OUTPUT_ROOT/logs"
RESULT_DIR="$OUTPUT_ROOT/results"
mkdir -p "$LOG_DIR" "$RESULT_DIR"

START_EPOCH="$(date +%s)"
COMMAND_SEQUENCE=(
    "blockMesh -case $OUTPUT_ROOT"
    "checkMesh -case $OUTPUT_ROOT"
    "simpleFoam -case $OUTPUT_ROOT"
)

blockMesh -case "$OUTPUT_ROOT" > "$LOG_DIR/blockMesh.log" 2>&1
test -s "$LOG_DIR/blockMesh.log"

checkMesh -case "$OUTPUT_ROOT" > "$LOG_DIR/checkMesh.log" 2>&1
test -s "$LOG_DIR/checkMesh.log"
grep -q "Mesh OK" "$LOG_DIR/checkMesh.log"

# The official OpenFOAM-10 pitzDaily Allrun sequence does not run potentialFoam.
POTENTIALFOAM_EXECUTED=0

set +e
simpleFoam -case "$OUTPUT_ROOT" > "$LOG_DIR/simpleFoam.log" 2>&1
SIMPLEFOAM_STATUS=$?
set -e
test -s "$LOG_DIR/simpleFoam.log"

"$PYTHON_BIN" "$ROOT_DIR/scripts/parse_simplefoam_log.py" \
    --log "$LOG_DIR/simpleFoam.log" \
    --results-dir "$RESULT_DIR" >/dev/null || true

LOWER_LOG="$RESULT_DIR/simpleFoam.lower.log"
tr '[:upper:]' '[:lower:]' < "$LOG_DIR/simpleFoam.log" > "$LOWER_LOG"
HAS_BAD_PATTERN=0
for pattern in \
    "nan" \
    "missing field" \
    "cannot find file" \
    "unknown turbulence model" \
    "foam fatal error"; do
    if grep -q "$pattern" "$LOWER_LOG"; then
        HAS_BAD_PATTERN=1
    fi
done
if grep -i "floating point exception" "$LOG_DIR/simpleFoam.log" | grep -vi "trapping" >/dev/null 2>&1; then
    HAS_BAD_PATTERN=1
fi
rm -f "$LOWER_LOG"

END_EPOCH="$(date +%s)"

"$PYTHON_BIN" - "$OUTPUT_ROOT" "$MODEL" "$MAX_ITERATIONS" "$SIMPLEFOAM_STATUS" "$HAS_BAD_PATTERN" "$START_EPOCH" "$END_EPOCH" "$POTENTIALFOAM_EXECUTED" <<'PY'
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


case_dir = Path(sys.argv[1])
model = sys.argv[2]
max_iterations_arg = sys.argv[3]
simplefoam_status = int(sys.argv[4])
has_bad_pattern = int(sys.argv[5]) == 1
start_epoch = int(sys.argv[6])
end_epoch = int(sys.argv[7])
potentialfoam_executed = sys.argv[8] == "1"


def read_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return {}


def git_commit():
    completed = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else None


def cell_count(check_mesh_log: Path):
    text = check_mesh_log.read_text(errors="ignore")
    match = re.search(r"\bcells:\s+([0-9]+)", text)
    return int(match.group(1)) if match else None


manifest = read_json(case_dir / "case_manifest.json")
summary = read_json(case_dir / "results/solver_summary.json")
if simplefoam_status != 0 or has_bad_pattern or summary.get("failed", True):
    status = "failed"
elif summary.get("converged"):
    status = "converged"
else:
    status = "max_iterations_reached"

metadata = {
    "model": model,
    "openfoam_version": os.environ.get("WM_PROJECT_VERSION", manifest.get("openfoam_version")),
    "solver_path": shutil.which("simpleFoam"),
    "cell_count": cell_count(case_dir / "logs/checkMesh.log"),
    "max_iterations": None if max_iterations_arg == "official" else int(max_iterations_arg),
    "actual_iterations": summary.get("actual_iterations"),
    "status": status,
    "converged": bool(summary.get("converged")),
    "max_iterations_reached": status == "max_iterations_reached",
    "failed": status == "failed",
    "final_residuals": summary.get("final_residuals", {}),
    "continuity_error": summary.get("final_continuity_error"),
    "wall_clock_seconds": max(0, end_epoch - start_epoch),
    "source_hashes": manifest.get("source_hashes", {}),
    "source_case_hashes": manifest.get("source_case_hashes", {}),
    "generated_case_hashes": manifest.get("generated_case_hashes", {}),
    "mesh_hash": manifest.get("mesh_hash"),
    "exact_command": [
        f"blockMesh -case {case_dir}",
        f"checkMesh -case {case_dir}",
        f"simpleFoam -case {case_dir}",
    ],
    "official_allrun_sequence": [
        "blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily",
        "simpleFoam",
    ],
    "potentialFoam_executed": potentialfoam_executed,
    "git_commit": git_commit(),
}
(case_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
PY

if [ "$SIMPLEFOAM_STATUS" -ne 0 ]; then
    echo "simpleFoam failed; see $LOG_DIR/simpleFoam.log" >&2
    exit "$SIMPLEFOAM_STATUS"
fi
if [ "$HAS_BAD_PATTERN" -ne 0 ]; then
    echo "simpleFoam log contains NaN, floating point exception, missing field, unknown turbulence model, or fatal error." >&2
    exit 1
fi

echo "RANS pitzDaily smoke run finished for $MODEL at $OUTPUT_ROOT"
