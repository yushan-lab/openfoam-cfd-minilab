#!/usr/bin/env python3
"""Generate paired CSV and figure outputs for formal pitzDaily RANS runs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import rans_pitzdaily_formal_tools as tools  # noqa: E402


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def replace_file_if_changed(candidate: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == candidate.read_bytes():
        candidate.unlink()
        return
    candidate.replace(output)


def save_figure_if_changed(fig: Any, output: Path, dpi: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate = output.with_name(f"{output.stem}.tmp{output.suffix}")
    fig.savefig(candidate, dpi=dpi)
    replace_file_if_changed(candidate, output)


def as_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def model_dirs(output_root: Path, k_epsilon: Path | None, k_omega_sst: Path | None) -> dict[str, Path]:
    return {
        "kEpsilon": k_epsilon or output_root / "kEpsilon",
        "kOmegaSST": k_omega_sst or output_root / "kOmegaSST",
    }


def display_label(model: str, summary: dict[str, Any] | None = None) -> str:
    if summary:
        return str(summary.get("display_model") or tools.model_display_name(model, summary.get("status")))
    return model


def build_model_summary(models: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in models.items():
        summary = read_json(case_dir / "results/solver_summary.json")
        pressure = read_csv(case_dir / "results/patch_pressure_summary.csv")
        yplus = read_csv(case_dir / "results/yplus_summary.csv")
        reattachment = read_json(case_dir / "results/reattachment_summary.json")
        lower_yplus = next((row for row in yplus if row.get("patch") == "lowerWall"), {})
        pressure_row = pressure[0] if pressure else {}
        status = summary.get("status")
        rows.append(
            {
                "model": model,
                "display_model": display_label(model, summary),
                "profile_name": summary.get("profile_name"),
                "status": status,
                "result_status": summary.get("result_status") or tools.result_status_for_model(status),
                "converged": summary.get("simple_solution_converged"),
                "iterations": summary.get("actual_iterations"),
                "wall_clock_seconds": summary.get("wall_clock_seconds"),
                "max_initial_residual_at_final_iteration": summary.get("max_initial_residual_at_final_iteration"),
                "residual_control_passed": summary.get("residual_control_passed"),
                "max_linear_solver_final_residual": summary.get("max_linear_solver_final_residual"),
                "relative_flow_imbalance": summary.get("relative_flow_imbalance"),
                "delta_p_in_minus_out_kinematic": pressure_row.get("delta_p_in_minus_out_kinematic")
                or summary.get("delta_p_in_minus_out_kinematic"),
                "pressure_recovery_kinematic": pressure_row.get("pressure_recovery_kinematic")
                or summary.get("pressure_recovery_kinematic"),
                "lowerWall_yplus_median": lower_yplus.get("median"),
                "lowerWall_yplus_p95": lower_yplus.get("p95"),
                "x_reattachment_raw": reattachment.get("x_reattachment_raw"),
                "reattachment_length_normalized": reattachment.get("reattachment_length_normalized"),
                "quality_gate_passed": summary.get("quality_gate_status") == "passed",
            }
        )
    return rows


def build_residual_summary(models: dict[str, Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    for model, case_dir in models.items():
        residuals = read_csv(case_dir / "results/residuals.csv")
        for row in residuals:
            row = {"model": model, **row}
            history_rows.append(row)
        by_field: dict[str, list[dict[str, Any]]] = {}
        for row in residuals:
            by_field.setdefault(row["field"], []).append(row)
        for field, rows in sorted(by_field.items()):
            final = rows[-1]
            summary_rows.append(
                {
                    "model": model,
                    "field": field,
                    "final_iteration": final.get("iteration"),
                    "initial_residual_at_final_solve": final.get("initial_residual"),
                    "linear_solver_final_residual": final.get("final_residual"),
                    "linear_iterations": final.get("linear_iterations"),
                }
            )
    return summary_rows, history_rows


def build_continuity_history(models: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in models.items():
        for row in read_csv(case_dir / "results/continuity_errors.csv"):
            rows.append({"model": model, **row})
    return rows


def combine_csv(models: dict[str, Path], relative_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in models.items():
        for row in read_csv(case_dir / relative_path):
            row.setdefault("model", model)
            rows.append(row)
    return rows


def combine_reattachment(models: dict[str, Path]) -> list[dict[str, Any]]:
    rows = []
    for model, case_dir in models.items():
        row = read_json(case_dir / "results/reattachment_summary.json")
        if row:
            row.setdefault("model", model)
            rows.append(row)
    return rows


def build_quality_gates(models: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in models.items():
        summary = read_json(case_dir / "results/solver_summary.json")
        gates = summary.get("quality_gates", {})
        for gate, passed in sorted(gates.items()):
            rows.append(
                {
                    "model": model,
                    "gate": gate,
                    "passed": passed,
                    "quality_gate_status": summary.get("quality_gate_status"),
                }
            )
    return rows


def plot_continuity(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for model in sorted({row["model"] for row in rows}):
        model_rows = [row for row in rows if row["model"] == model]
        xs = [int(float(row["iteration"])) for row in model_rows]
        ys = [abs(float(row["global"])) for row in model_rows]
        if xs:
            ax.semilogy(xs, ys, label=model)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Absolute global continuity error")
    ax.set_title("Continuity Error Comparison")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    save_figure(fig, output)


def plot_yplus(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    labels: list[str] = []
    values: list[list[float]] = []
    for model in ["kEpsilon", "kOmegaSST"]:
        for patch in ["lowerWall", "upperWall"]:
            vals = [float(row["yplus"]) for row in rows if row["model"] == model and row["patch"] == patch]
            if vals:
                model_label = next(
                    (row.get("display_model") or model for row in rows if row["model"] == model and row["patch"] == patch),
                    model,
                )
                labels.append(f"{model_label}\n{patch}")
                values.append(vals)
    if values:
        ax.boxplot(values, tick_labels=labels, showfliers=False)
    ax.set_ylabel("y+")
    ax.set_title("y+ Distribution Comparison")
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output)


def plot_wall_shear(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for model in sorted({row["model"] for row in rows}):
        model_rows = sorted(
            [row for row in rows if row["model"] == model and row["patch"] == "lowerWall"],
            key=lambda row: float(row["x"]),
        )
        if model_rows:
            model_label = model_rows[0].get("display_model") or model
            ax.plot(
                [float(row["x"]) for row in model_rows],
                [float(row.get("tau_downstream_tangent") or row["tau_streamwise"]) for row in model_rows],
                label=model_label,
            )
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("x")
    ax.set_ylabel("Kinematic wall shear stress, downstream tangent component")
    ax.set_title("Lower-Wall Shear Comparison")
    ax.grid(True, alpha=0.3)
    ax.legend()
    save_figure(fig, output)


def plot_reattachment(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    detected = [row for row in rows if row.get("status") == "detected" and row.get("x_reattachment_raw") is not None]
    if detected:
        ax.bar(
            [row.get("display_model") or row["model"] for row in detected],
            [float(row["x_reattachment_raw"]) for row in detected],
        )
    ax.set_ylabel("Raw x reattachment location")
    ax.set_title("Reattachment Diagnostic")
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output)


def plot_pressure(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    usable = [row for row in rows if row.get("pressure_recovery_kinematic") not in {None, ""}]
    if usable:
        ax.bar(
            [row.get("display_model") or row["model"] for row in usable],
            [float(row["pressure_recovery_kinematic"]) for row in usable],
        )
    ax.set_ylabel("Kinematic pressure recovery")
    ax.set_title("Pressure Recovery Comparison")
    ax.grid(True, axis="y", alpha=0.3)
    save_figure(fig, output)


def plot_iterations_and_cost(rows: list[dict[str, Any]], output: Path) -> None:
    fig, ax1 = plt.subplots(figsize=(8, 5))
    xs = list(range(len(rows)))
    labels = [row.get("display_model") or row["model"] for row in rows]
    iterations = [as_float(row.get("iterations")) or 0.0 for row in rows]
    costs = [as_float(row.get("wall_clock_seconds")) or 0.0 for row in rows]
    ax1.bar([x - 0.18 for x in xs], iterations, width=0.36, label="iterations")
    ax1.set_ylabel("Iterations")
    ax2 = ax1.twinx()
    ax2.bar([x + 0.18 for x in xs], costs, width=0.36, color="tab:orange", label="wall-clock seconds")
    ax2.set_ylabel("Wall-clock seconds")
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels)
    ax1.set_title("Iterations and Compute Cost")
    ax1.grid(True, axis="y", alpha=0.3)
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2)
    save_figure(fig, output)


def save_figure(fig: plt.Figure, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    save_figure_if_changed(fig, output, dpi=160)
    plt.close(fig)


def generate_comparison(output_root: Path, k_epsilon: Path | None = None, k_omega_sst: Path | None = None) -> dict[str, Path]:
    models = model_dirs(output_root, k_epsilon, k_omega_sst)
    comparison = output_root / "comparison"
    figure_dir = comparison / "figures"
    comparison.mkdir(parents=True, exist_ok=True)

    model_summary = build_model_summary(models)
    residual_summary, residual_history = build_residual_summary(models)
    continuity_history = build_continuity_history(models)
    flow_rows = combine_csv(models, "results/patch_flow_rates.csv")
    pressure_rows = combine_csv(models, "results/patch_pressure_summary.csv")
    flow_pressure = flow_rows + pressure_rows
    yplus_summary = combine_csv(models, "results/yplus_summary.csv")
    yplus_values = combine_csv(models, "results/yplus_patch_values.csv")
    wall_shear = combine_csv(models, "results/wall_shear_stress_values.csv")
    reattachment = combine_reattachment(models)
    field_stability = combine_csv(models, "results/field_stability_summary.csv")
    quality_gates = build_quality_gates(models)

    tools.write_csv(comparison / "model_summary.csv", model_summary, tools.MODEL_SUMMARY_FIELDS)
    tools.write_csv(
        comparison / "residual_summary.csv",
        residual_summary,
        [
            "model",
            "field",
            "final_iteration",
            "initial_residual_at_final_solve",
            "linear_solver_final_residual",
            "linear_iterations",
        ],
    )
    tools.write_csv(comparison / "residual_history.csv", residual_history)
    tools.write_csv(comparison / "continuity_error_history.csv", continuity_history)
    tools.write_csv(comparison / "flow_pressure_summary.csv", flow_pressure)
    tools.write_csv(comparison / "yplus_summary.csv", yplus_summary)
    tools.write_csv(comparison / "reattachment_summary.csv", reattachment)
    tools.write_csv(comparison / "field_stability_summary.csv", field_stability)
    tools.write_csv(comparison / "pair_quality_gates.csv", quality_gates)

    tools.write_residual_control_figures(models, figure_dir)
    plot_continuity(continuity_history, figure_dir / "continuity_error_comparison.png")
    plot_yplus(yplus_values, figure_dir / "yplus_distribution_comparison.png")
    plot_wall_shear(wall_shear, figure_dir / "lower_wall_shear_comparison.png")
    plot_reattachment(reattachment, figure_dir / "reattachment_location_comparison.png")
    plot_pressure(pressure_rows, figure_dir / "pressure_recovery_comparison.png")
    plot_iterations_and_cost(model_summary, figure_dir / "iterations_and_cost_comparison.png")

    return {
        "comparison": comparison,
        "figures": figure_dir,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/rans_pitzdaily_formal_v2")
    parser.add_argument("--k-epsilon", type=Path)
    parser.add_argument("--k-omega-sst", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate_comparison(args.output_root, args.k_epsilon, args.k_omega_sst)
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
