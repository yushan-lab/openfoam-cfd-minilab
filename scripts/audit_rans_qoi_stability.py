#!/usr/bin/env python3
"""Audit pitzDaily RANS QoI stability using existing fields and explicit-time postProcess."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import rans_pitzdaily_formal_tools as tools  # noqa: E402


MODELS = ("kEpsilon", "kOmegaSST")
THRESHOLDS = {
    "pressure_recovery_relative_change": 0.02,
    "reattachment_relative_change": 0.02,
    "lower_yplus_median_relative_change": 0.02,
    "lower_yplus_p95_relative_change": 0.02,
    "wall_shear_curve_relative_L2": 0.03,
    "final_flow_imbalance": 0.01,
}


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def relative_change(previous: float | None, final: float | None) -> float | None:
    if previous is None or final is None:
        return None
    return abs(final - previous) / max(abs(previous), abs(final), 1e-30)


def relative_l2(previous: list[float], final: list[float]) -> float | None:
    if not previous or len(previous) != len(final):
        return None
    numerator = math.sqrt(sum((b - a) ** 2 for a, b in zip(previous, final)))
    denominator = math.sqrt(sum(value * value for value in final))
    return numerator / max(denominator, 1e-30)


def explicit_time_postprocess_command(case_dir: Path, time_name: str, function_name: str) -> list[str]:
    if function_name in {"yPlus", "wallShearStress"}:
        return ["simpleFoam", "-case", str(case_dir), "-postProcess", "-time", str(time_name), "-func", function_name]
    return ["postProcess", "-case", str(case_dir), "-time", str(time_name), "-func", function_name]


def continuation_plan_required(qoi_rows: list[dict[str, Any]]) -> bool:
    return any(str(row.get("qoi_stability_passed")).lower() != "true" for row in qoi_rows)


def continuation_already_performed(output_root: Path, selected_profile: str) -> bool:
    return output_root.name == "stability_continuation" or selected_profile == "continuation_common"


def continuation_plan_for_both_models(output_root: Path, selected_profile: str) -> dict[str, Any]:
    if continuation_already_performed(output_root, selected_profile):
        return {
            "required": False,
            "allowed": False,
            "reason": "fixed shared +300-iteration continuation already performed; no further continuation is allowed by the protocol",
        }
    continuation_root = output_root / "stability_continuation"
    return {
        "required": True,
        "models": list(MODELS),
        "selected_profile": selected_profile,
        "output_root": continuation_root.as_posix(),
        "additional_iterations": 300,
        "write_interval": 100,
        "same_settings_for_both_models": True,
        "disable_residual_control_for_fixed_extension": True,
    }


def stability_times(output_root: Path) -> dict[str, dict[str, Any]]:
    rows = read_csv_rows(output_root / "final_audit/selected_field_stability.csv")
    result: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        model_rows = [
            row
            for row in rows
            if row.get("model") == model and row.get("field") == "U" and row.get("requested_offset_iterations") == "100"
        ]
        if not model_rows:
            raise ValueError(f"Missing selected_field_stability 100-iteration U row for {model}")
        row = model_rows[0]
        result[model] = {
            "comparison_iteration": row["comparison_time"],
            "final_iteration": row["final_time"],
            "actual_offset_iterations": float(row["actual_offset_iterations"]),
        }
    return result


def required_field_names(model: str) -> list[str]:
    return ["U", "p", "k", "nut", "phi", "epsilon" if model == "kEpsilon" else "omega"]


def verify_required_fields(case_dir: Path, model: str, time_name: str) -> dict[str, Any]:
    fields = required_field_names(model)
    checks = {field: (case_dir / str(time_name) / field).exists() for field in fields}
    return {"time": time_name, "all_present": all(checks.values()), "fields": checks}


def run_postprocess(case_dir: Path, model: str, time_name: str, log_dir: Path, dry_run: bool = False) -> list[dict[str, Any]]:
    functions = [
        "patchFlowRate(phi,patch=inlet)",
        "patchFlowRate(phi,patch=outlet)",
        "patchAverage(p,patch=inlet)",
        "patchAverage(p,patch=outlet)",
        "yPlus",
        "wallShearStress",
    ]
    results = []
    log_dir.mkdir(parents=True, exist_ok=True)
    for function_name in functions:
        command = explicit_time_postprocess_command(case_dir, time_name, function_name)
        safe_name = (
            function_name.replace("(", "_")
            .replace(")", "")
            .replace(",", "_")
            .replace("=", "-")
            .replace("/", "-")
        )
        log_path = log_dir / f"{model}_{time_name}_{safe_name}.log"
        if dry_run:
            status = 0
            stdout = ""
            stderr = ""
        else:
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            status = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            log_path.write_text(stdout + stderr, encoding="utf-8")
        results.append(
            {
                "model": model,
                "time": time_name,
                "function": function_name,
                "command": " ".join(command),
                "log": log_path.as_posix(),
                "returncode": status,
                "succeeded": status == 0,
                "used_explicit_time": "-time" in command and str(time_name) in command,
                "postprocess_mode": "-postProcess" in command or command[0] == "postProcess",
            }
        )
    return results


def postprocessing_dat(case_dir: Path, function_name: str, time_name: str) -> Path:
    return case_dir / "postProcessing" / function_name / str(time_name) / "surfaceFieldValue.dat"


def pressure_recovery(case_dir: Path, time_name: str) -> float | None:
    inlet = tools.parse_surface_value_dat(postprocessing_dat(case_dir, "patchAverage(p,patch=inlet)", time_name))
    outlet = tools.parse_surface_value_dat(postprocessing_dat(case_dir, "patchAverage(p,patch=outlet)", time_name))
    if inlet is None or outlet is None:
        return None
    return outlet - inlet


def flow_imbalance(case_dir: Path, time_name: str) -> float | None:
    rows = []
    for patch in ["inlet", "outlet"]:
        value = tools.parse_surface_value_dat(postprocessing_dat(case_dir, f"patchFlowRate(phi,patch={patch})", time_name))
        if value is None:
            return None
        rows.append({"patch": patch, "signed_volumetric_flow_rate": value})
    return tools.relative_flow_imbalance(rows)


def yplus_summary(case_dir: Path, model: str, time_name: str) -> dict[str, dict[str, Any]]:
    values, summaries = tools.collect_yplus(case_dir, model, str(time_name))
    del values
    return {row["patch"]: row for row in summaries}


def lower_wall_shear_curve(case_dir: Path, model: str, time_name: str) -> list[float]:
    rows = [
        row
        for row in tools.collect_wall_shear(case_dir, model, str(time_name))
        if row.get("patch") == "lowerWall"
    ]
    rows = sorted(rows, key=lambda row: int(row["face_index"]))
    return [float(row.get("tau_downstream_tangent") or row["tau_streamwise"]) for row in rows]


def reattachment_length(case_dir: Path, model: str, time_name: str) -> dict[str, Any]:
    wall_shear = tools.collect_wall_shear(case_dir, model, str(time_name))
    return tools.collect_reattachment(case_dir, wall_shear)


def qoi_at_time(case_dir: Path, model: str, time_name: str) -> dict[str, Any]:
    yplus = yplus_summary(case_dir, model, time_name)
    reattachment = reattachment_length(case_dir, model, time_name)
    return {
        "pressure_recovery": pressure_recovery(case_dir, time_name),
        "flow_imbalance": flow_imbalance(case_dir, time_name),
        "lower_yplus_median": as_float(yplus.get("lowerWall", {}).get("median")),
        "lower_yplus_p95": as_float(yplus.get("lowerWall", {}).get("p95")),
        "upper_yplus_median": as_float(yplus.get("upperWall", {}).get("median")),
        "upper_yplus_p95": as_float(yplus.get("upperWall", {}).get("p95")),
        "reattachment_length": as_float(reattachment.get("reattachment_length_normalized")),
        "reattachment": reattachment,
        "lower_wall_shear_curve": lower_wall_shear_curve(case_dir, model, time_name),
    }


def stability_row(model: str, previous: dict[str, Any], final: dict[str, Any], times: dict[str, Any]) -> dict[str, Any]:
    pressure_change = relative_change(previous["pressure_recovery"], final["pressure_recovery"])
    reattachment_change = relative_change(previous["reattachment_length"], final["reattachment_length"])
    y_median_change = relative_change(previous["lower_yplus_median"], final["lower_yplus_median"])
    y_p95_change = relative_change(previous["lower_yplus_p95"], final["lower_yplus_p95"])
    shear_l2 = relative_l2(previous["lower_wall_shear_curve"], final["lower_wall_shear_curve"])
    final_flow = final["flow_imbalance"]
    gates = {
        "pressure_recovery": pressure_change is not None and pressure_change <= THRESHOLDS["pressure_recovery_relative_change"],
        "reattachment": reattachment_change is not None and reattachment_change <= THRESHOLDS["reattachment_relative_change"],
        "lower_yplus_median": y_median_change is not None and y_median_change <= THRESHOLDS["lower_yplus_median_relative_change"],
        "lower_yplus_p95": y_p95_change is not None and y_p95_change <= THRESHOLDS["lower_yplus_p95_relative_change"],
        "wall_shear_curve": shear_l2 is not None and shear_l2 <= THRESHOLDS["wall_shear_curve_relative_L2"],
        "final_flow_imbalance": final_flow is not None and final_flow <= THRESHOLDS["final_flow_imbalance"],
    }
    return {
        "model": model,
        "comparison_iteration": times["comparison_iteration"],
        "final_iteration": times["final_iteration"],
        "actual_offset_iterations": times["actual_offset_iterations"],
        "pressure_recovery_previous": previous["pressure_recovery"],
        "pressure_recovery_final": final["pressure_recovery"],
        "pressure_recovery_relative_change": pressure_change,
        "reattachment_length_previous": previous["reattachment_length"],
        "reattachment_length_final": final["reattachment_length"],
        "reattachment_relative_change": reattachment_change,
        "lower_yplus_median_previous": previous["lower_yplus_median"],
        "lower_yplus_median_final": final["lower_yplus_median"],
        "lower_yplus_median_relative_change": y_median_change,
        "lower_yplus_p95_previous": previous["lower_yplus_p95"],
        "lower_yplus_p95_final": final["lower_yplus_p95"],
        "lower_yplus_p95_relative_change": y_p95_change,
        "wall_shear_curve_relative_L2": shear_l2,
        "flow_imbalance_previous": previous["flow_imbalance"],
        "flow_imbalance_final": final["flow_imbalance"],
        "pressure_recovery_gate": gates["pressure_recovery"],
        "reattachment_gate": gates["reattachment"],
        "lower_yplus_median_gate": gates["lower_yplus_median"],
        "lower_yplus_p95_gate": gates["lower_yplus_p95"],
        "wall_shear_curve_gate": gates["wall_shear_curve"],
        "final_flow_imbalance_gate": gates["final_flow_imbalance"],
        "qoi_stability_passed": all(gates.values()),
    }


def audit_qoi_stability(output_root: Path, selected_profile: str, dry_run: bool = False) -> dict[str, Any]:
    times_by_model = stability_times(output_root)
    final_audit = output_root / "final_audit"
    log_dir = final_audit / "qoi_postprocess_logs"
    rows = []
    postprocess_results = []
    required_field_checks = {}
    reattachment_details = {}
    for model in MODELS:
        case_dir = output_root / selected_profile / model
        times = times_by_model[model]
        required_field_checks[model] = {
            name: verify_required_fields(case_dir, model, times[name])
            for name in ["comparison_iteration", "final_iteration"]
        }
        for time_name in [times["comparison_iteration"], times["final_iteration"]]:
            postprocess_results.extend(run_postprocess(case_dir, model, str(time_name), log_dir, dry_run=dry_run))
        previous = qoi_at_time(case_dir, model, str(times["comparison_iteration"]))
        final = qoi_at_time(case_dir, model, str(times["final_iteration"]))
        row = stability_row(model, previous, final, times)
        rows.append(row)
        reattachment_details[model] = {
            "previous": previous["reattachment"],
            "final": final["reattachment"],
        }

    continuation_required = continuation_plan_required(rows)
    continuation_plan = continuation_plan_for_both_models(output_root, selected_profile) if continuation_required else {
        "required": False,
        "reason": "both models passed QoI stability gates",
    }
    fieldnames = [
        "model",
        "comparison_iteration",
        "final_iteration",
        "actual_offset_iterations",
        "pressure_recovery_previous",
        "pressure_recovery_final",
        "pressure_recovery_relative_change",
        "reattachment_length_previous",
        "reattachment_length_final",
        "reattachment_relative_change",
        "lower_yplus_median_previous",
        "lower_yplus_median_final",
        "lower_yplus_median_relative_change",
        "lower_yplus_p95_previous",
        "lower_yplus_p95_final",
        "lower_yplus_p95_relative_change",
        "wall_shear_curve_relative_L2",
        "flow_imbalance_previous",
        "flow_imbalance_final",
        "pressure_recovery_gate",
        "reattachment_gate",
        "lower_yplus_median_gate",
        "lower_yplus_p95_gate",
        "wall_shear_curve_gate",
        "final_flow_imbalance_gate",
        "qoi_stability_passed",
    ]
    write_csv(final_audit / "qoi_stability.csv", rows, fieldnames)
    payload = {
        "selected_profile": selected_profile,
        "thresholds": THRESHOLDS,
        "required_field_checks": required_field_checks,
        "postprocess_results": postprocess_results,
        "rows": rows,
        "qoi_stability_passed": all(row["qoi_stability_passed"] for row in rows),
        "comparison_status": "stability-audited formal comparison"
        if all(row["qoi_stability_passed"] for row in rows)
        else "quality_incomplete_comparison",
        "continuation": continuation_plan,
        "reattachment_details": reattachment_details,
    }
    write_json(final_audit / "qoi_stability.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/rans_pitzdaily_formal_v2")
    parser.add_argument("--selected-profile", default="conservative_common")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit_qoi_stability(args.output_root, args.selected_profile, args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
