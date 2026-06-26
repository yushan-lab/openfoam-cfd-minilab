#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/runs/rans_pitzdaily_formal_v2}"
OVERWRITE="${OVERWRITE:-0}"

set +e
set +u
if [ -f /opt/openfoam10/etc/bashrc ] && [ "${WM_PROJECT_VERSION:-}" != "10" ]; then
    # shellcheck disable=SC1091
    source /opt/openfoam10/etc/bashrc
fi
set -e
set -u

for cmd in blockMesh checkMesh simpleFoam postProcess; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Required OpenFOAM command not found: $cmd" >&2
        exit 1
    }
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
    echo "Required Python command not found: $PYTHON_BIN" >&2
    exit 1
}

OUTPUT_ROOT="$(cd "$(dirname "$OUTPUT_ROOT")" && pwd)/$(basename "$OUTPUT_ROOT")"
case "$OUTPUT_ROOT" in
    "$ROOT_DIR"/runs/rans_pitzdaily_formal_v2*) ;;
    *)
        echo "Refusing OUTPUT_ROOT outside the v2 formal RANS runs area: $OUTPUT_ROOT" >&2
        exit 1
        ;;
esac

if [ -e "$OUTPUT_ROOT" ] && [ "$(find "$OUTPUT_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)" -gt 0 ]; then
    if [ "$OVERWRITE" != "1" ]; then
        echo "Refusing to overwrite non-empty output directory: $OUTPUT_ROOT" >&2
        echo "Set OVERWRITE=1 to regenerate v2 formal outputs from clean initial conditions." >&2
        exit 1
    fi
    rm -rf "$OUTPUT_ROOT"
fi
mkdir -p "$OUTPUT_ROOT/_audit" "$OUTPUT_ROOT/selected" "$OUTPUT_ROOT/comparison"

"$PYTHON_BIN" "$ROOT_DIR/scripts/rans_pitzdaily_formal_tools.py" freeze-v1 \
    --v1-root "$ROOT_DIR/runs/rans_pitzdaily_formal" \
    --output "$OUTPUT_ROOT/_audit/v1_hashes.json" >/dev/null

prepare_case() {
    local profile_name="$1"
    local model="$2"
    local case_dir="$3"
    local iterations="$4"
    local u_relaxation="$5"
    local equation_catch_all_relaxation="$6"

    "$PYTHON_BIN" "$ROOT_DIR/scripts/prepare_rans_pitzdaily_case.py" \
        --model "$model" \
        --output "$case_dir" \
        --max-iterations "$iterations" \
        --profile-name "$profile_name" \
        --u-relaxation "$u_relaxation" \
        --equation-catch-all-relaxation "$equation_catch_all_relaxation" \
        --overwrite >/dev/null
}

write_failure_summary() {
    local case_dir="$1"
    local model="$2"
    local profile_name="$3"
    local reason="$4"
    local wall_clock="$5"
    local start_epoch="$6"
    local end_epoch="$7"

    "$PYTHON_BIN" - "$case_dir" "$model" "$profile_name" "$reason" "$wall_clock" "$start_epoch" "$end_epoch" <<'PY'
import json
from pathlib import Path
import sys

case_dir = Path(sys.argv[1])
model = sys.argv[2]
profile_name = sys.argv[3]
reason = sys.argv[4]
wall_clock = float(sys.argv[5])
start_epoch = int(sys.argv[6])
end_epoch = int(sys.argv[7])
result_dir = case_dir / "results"
result_dir.mkdir(parents=True, exist_ok=True)
summary = {
    "model": model,
    "display_model": f"{model} (not converged)",
    "profile_name": profile_name,
    "status": "failed",
    "result_status": "provisional_incomplete_convergence",
    "actual_iterations": 0,
    "simple_solution_converged": False,
    "final_residuals": {},
    "max_initial_residual_at_final_iteration": None,
    "residual_control_passed": False,
    "max_linear_solver_final_residual": None,
    "max_final_residual": None,
    "final_continuity_error": None,
    "failure_reasons": [reason],
    "run_start_epoch": start_epoch,
    "run_end_epoch": end_epoch,
    "wall_clock_seconds": wall_clock,
    "quality_gate_status": "failed",
}
(result_dir / "solver_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
PY
}

run_postprocess_function() {
    local case_dir="$1"
    local log_file="$2"
    shift 2

    set +e
    postProcess -case "$case_dir" -latestTime "$@" > "$log_file" 2>&1
    local status=$?
    set -e
    echo "postProcess status $status for $*" >> "$log_file"
    return 0
}

run_simplefoam_postprocess_function() {
    local case_dir="$1"
    local log_file="$2"
    shift 2

    set +e
    simpleFoam -case "$case_dir" -postProcess -latestTime "$@" > "$log_file" 2>&1
    local status=$?
    set -e
    echo "simpleFoam -postProcess status $status for $*" >> "$log_file"
    return 0
}

run_model() {
    local profile_name="$1"
    local model="$2"
    local case_dir="$3"
    local pair_audit_passed="$4"
    local log_dir="$case_dir/logs"
    local result_dir="$case_dir/results"
    mkdir -p "$log_dir" "$result_dir"

    local start_epoch
    start_epoch="$(date +%s)"

    set +e
    blockMesh -case "$case_dir" > "$log_dir/blockMesh.log" 2>&1
    local block_status=$?
    set -e
    if [ "$block_status" -ne 0 ]; then
        local end_epoch
        end_epoch="$(date +%s)"
        write_failure_summary "$case_dir" "$model" "$profile_name" "blockMesh_failed" "$((end_epoch - start_epoch))" "$start_epoch" "$end_epoch"
        return 0
    fi

    set +e
    checkMesh -case "$case_dir" > "$log_dir/checkMesh.log" 2>&1
    local check_status=$?
    set -e
    if [ "$check_status" -ne 0 ] || ! grep -q "Mesh OK" "$log_dir/checkMesh.log"; then
        local end_epoch
        end_epoch="$(date +%s)"
        write_failure_summary "$case_dir" "$model" "$profile_name" "checkMesh_failed" "$((end_epoch - start_epoch))" "$start_epoch" "$end_epoch"
        return 0
    fi

    set +e
    simpleFoam -case "$case_dir" > "$log_dir/simpleFoam.log" 2>&1
    local simple_status=$?
    set -e

    "$PYTHON_BIN" "$ROOT_DIR/scripts/parse_simplefoam_log.py" \
        --log "$log_dir/simpleFoam.log" \
        --results-dir "$result_dir" >/dev/null || true

    run_simplefoam_postprocess_function "$case_dir" "$log_dir/postProcess_yPlus.log" -func yPlus
    run_simplefoam_postprocess_function "$case_dir" "$log_dir/postProcess_wallShearStress.log" -func wallShearStress
    run_postprocess_function "$case_dir" "$log_dir/postProcess_writeCellCentres.log" -func writeCellCentres
    run_postprocess_function "$case_dir" "$log_dir/postProcess_writeCellVolumes.log" -func writeCellVolumes
    for patch in inlet outlet; do
        run_postprocess_function "$case_dir" "$log_dir/postProcess_patchFlowRate_${patch}.log" \
            -func "patchFlowRate(phi,patch=$patch)"
        run_postprocess_function "$case_dir" "$log_dir/postProcess_patchAverage_p_${patch}.log" \
            -func "patchAverage(p,patch=$patch)"
    done

    local end_epoch
    end_epoch="$(date +%s)"
    local pair_flag=()
    if [ "$pair_audit_passed" = "true" ]; then
        pair_flag=(--pair-audit-passed)
    fi
    "$PYTHON_BIN" "$ROOT_DIR/scripts/rans_pitzdaily_formal_tools.py" postprocess-case \
        --case-dir "$case_dir" \
        --model "$model" \
        --profile-name "$profile_name" \
        --wall-clock-seconds "$((end_epoch - start_epoch))" \
        --run-start-epoch "$start_epoch" \
        --run-end-epoch "$end_epoch" \
        --solver-exit-code "$simple_status" \
        "${pair_flag[@]}" >/dev/null || true
}

write_profile_manifest() {
    local profile_root="$1"
    local profile_name="$2"
    "$PYTHON_BIN" - "$profile_root" "$profile_name" <<'PY'
import csv
import json
import os
import subprocess
from pathlib import Path
import sys

root = Path(sys.argv[1])
profile_name = sys.argv[2]
models = ["kEpsilon", "kOmegaSST"]
commit = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip()
version = os.environ.get("WM_PROJECT_VERSION", "")
fields = [
    "model",
    "profile_name",
    "status",
    "iterations",
    "output_root",
    "wall_clock_seconds",
    "git_commit",
    "openfoam_version",
    "pair_audit_passed",
]
rows = []
for model in models:
    case_dir = root / model
    summary_path = case_dir / "results/solver_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"status": "failed"}
    rows.append(
        {
            "model": model,
            "profile_name": profile_name,
            "status": summary.get("status", "failed"),
            "iterations": summary.get("actual_iterations"),
            "output_root": str(case_dir),
            "wall_clock_seconds": summary.get("wall_clock_seconds"),
            "git_commit": commit,
            "openfoam_version": version,
            "pair_audit_passed": True,
        }
    )
with (root / "manifest.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
PY
}

both_models_converged() {
    local profile_root="$1"
    "$PYTHON_BIN" - "$profile_root" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
statuses = []
for model in ["kEpsilon", "kOmegaSST"]:
    path = root / model / "results/solver_summary.json"
    statuses.append(json.loads(path.read_text()).get("status", "failed") if path.exists() else "failed")
print("1" if all(status == "converged" for status in statuses) else "0")
PY
}

run_pair() {
    local profile_name="$1"
    local iterations="$2"
    local u_relaxation="$3"
    local equation_catch_all_relaxation="$4"
    local profile_root="$OUTPUT_ROOT/$profile_name"

    echo "Preparing clean paired cases for $profile_name ($iterations iterations)"
    mkdir -p "$profile_root"
    prepare_case "$profile_name" kEpsilon "$profile_root/kEpsilon" "$iterations" "$u_relaxation" "$equation_catch_all_relaxation"
    prepare_case "$profile_name" kOmegaSST "$profile_root/kOmegaSST" "$iterations" "$u_relaxation" "$equation_catch_all_relaxation"

    "$PYTHON_BIN" "$ROOT_DIR/scripts/audit_rans_case_pair.py" \
        --k-epsilon "$profile_root/kEpsilon" \
        --k-omega-sst "$profile_root/kOmegaSST" \
        --output "$profile_root/pair_audit.json" >/dev/null

    "$PYTHON_BIN" "$ROOT_DIR/scripts/rans_pitzdaily_formal_tools.py" audit-setup \
        --k-epsilon "$profile_root/kEpsilon" \
        --k-omega-sst "$profile_root/kOmegaSST" \
        --output-dir "$profile_root" >/dev/null

    run_model "$profile_name" kEpsilon "$profile_root/kEpsilon" true
    run_model "$profile_name" kOmegaSST "$profile_root/kOmegaSST" true
    write_profile_manifest "$profile_root" "$profile_name"
}

run_pair official_common 4000 0.9 0.9

SELECTED_PROFILE="official_common"
FALLBACK_TRIGGERED=0
if [ "$(both_models_converged "$OUTPUT_ROOT/official_common")" != "1" ]; then
    FALLBACK_TRIGGERED=1
    run_pair conservative_common 8000 0.7 0.5
    SELECTED_PROFILE="conservative_common"
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/rans_pitzdaily_formal_tools.py" audit-postprocess \
    --case-dir "$OUTPUT_ROOT/$SELECTED_PROFILE/kEpsilon" \
    --output "$OUTPUT_ROOT/_audit/postprocessing_capabilities.json" >/dev/null

"$PYTHON_BIN" "$ROOT_DIR/scripts/compare_rans_pitzdaily_models.py" \
    --output-root "$OUTPUT_ROOT" \
    --k-epsilon "$OUTPUT_ROOT/$SELECTED_PROFILE/kEpsilon" \
    --k-omega-sst "$OUTPUT_ROOT/$SELECTED_PROFILE/kOmegaSST"

"$PYTHON_BIN" - "$OUTPUT_ROOT" "$SELECTED_PROFILE" "$FALLBACK_TRIGGERED" <<'PY'
import csv
import json
import shutil
from pathlib import Path
import sys

root = Path(sys.argv[1])
selected_profile = sys.argv[2]
fallback_triggered = sys.argv[3] == "1"
models = ["kEpsilon", "kOmegaSST"]
selected_root = root / "selected"
selected_root.mkdir(parents=True, exist_ok=True)
statuses = {}
quality = {}
required_ready = {}
for model in models:
    summary = json.loads((root / selected_profile / model / "results/solver_summary.json").read_text())
    gates = summary.get("quality_gates", {})
    statuses[model] = summary.get("status", "failed")
    quality[model] = summary.get("quality_gate_status", "failed")
    required_ready[model] = (
        summary.get("status") == "converged"
        and summary.get("quality_gate_status") == "passed"
        and bool(gates.get("pair_audit"))
        and bool(gates.get("flow_balance"))
        and bool(gates.get("yPlus"))
        and bool(gates.get("wallShearStress"))
        and bool(gates.get("patch_pressure"))
        and not any(summary.get(key) for key in [
            "has_nan",
            "has_floating_point_exception",
            "has_fatal_error",
            "has_missing_field",
            "has_unknown_model",
        ])
    )
both_converged = all(status == "converged" for status in statuses.values())
comparison_ready = all(required_ready.values())
selection = {
    "selected_profile": selected_profile,
    "profiles_attempted": ["official_common", "conservative_common"] if fallback_triggered else ["official_common"],
    "fallback_triggered": fallback_triggered,
    "both_models_converged": both_converged,
    "model_statuses": statuses,
    "quality_gate_statuses": quality,
    "required_quality_ready": required_ready,
    "selection_reason": "first common profile for which both models converged and passed required quality gates",
    "selection_basis": "convergence and required quality gates only",
    "comparison_status": "formal_comparison" if comparison_ready else "quality_incomplete_comparison",
}
(selected_root / "selection_summary.json").write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n")
source_summary = root / "comparison/model_summary.csv"
if source_summary.exists():
    shutil.copy2(source_summary, selected_root / "model_summary.csv")
profile_pointer = selected_root / "case_locations.csv"
with profile_pointer.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["model", "selected_profile", "case_dir"])
    writer.writeheader()
    for model in models:
        writer.writerow({"model": model, "selected_profile": selected_profile, "case_dir": root / selected_profile / model})
PY

"$PYTHON_BIN" "$ROOT_DIR/scripts/rans_pitzdaily_formal_tools.py" final-audit \
    --output-root "$OUTPUT_ROOT" \
    --selected-profile "$SELECTED_PROFILE" >/dev/null

echo "Formal pitzDaily RANS comparison outputs written to $OUTPUT_ROOT"
