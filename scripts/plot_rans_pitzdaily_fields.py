#!/usr/bin/env python3
"""Plot selected pitzDaily RANS final fields and export lightweight public results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import rans_pitzdaily_formal_tools as tools  # noqa: E402


MODELS = ("kEpsilon", "kOmegaSST")
MODEL_COLORS = tools.MODEL_COLORS
PRESSURE_LABEL = "kinematic pressure (m^2/s^2)"
PUBLIC_SUMMARY_TEXT = """RANS pitzDaily paired diagnostic

Method: steady incompressible RANS solutions were post-processed from the selected {selected_profile} final fields.

Fairness settings: both models used the same mesh, boundary conditions, numerical schemes, and shared relaxation profile. The selected profile uses U_exact_entry = 0.7 and equation_catch_all_regex = ".*" with equation_catch_all_value = 0.5.

Project positioning: paired RANS model diagnostic and stability-boundary study.

Numerical result summary:
{table}

Post-convergence stability:
- {stability_status}
- exact QoI changes are available in qoi_stability.csv

Diagnostic status: {comparison_status_line}

Interpretation boundary: kOmegaSST used more iterations. The two models give different pressure recovery, y+ and reattachment predictions; kOmegaSST predicts a longer reattachment length in this run.

Primary figures:
- figures/rans_pitzdaily/residual_by_field_comparison.png
- figures/rans_pitzdaily/field_velocity_comparison.png
- figures/rans_pitzdaily/field_model_difference.png

Limitations: these figures compare two model predictions on one mesh and one setup. They are not experimental validation, not a turbulence-model accuracy ranking, and not a grid-independence study.
"""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def copy_if_changed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        return
    shutil.copy2(src, dst)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_internal_field(path: Path) -> tuple[str | None, np.ndarray]:
    text = path.read_text(errors="ignore")
    dimensions = tools.parse_dimensions(text)
    match = re.search(r"internalField\s+(.*?);", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Missing internalField in {path}")
    block = match.group(1)
    values = tools._parse_nonuniform_values(block)
    if values is None:
        parsed = tools._parse_uniform_value_from_line(block, None)
        values = [parsed]
    array = np.asarray(values, dtype=float)
    return dimensions, array


def flatten_numeric(value: np.ndarray) -> np.ndarray:
    return np.asarray(value, dtype=float).reshape(-1)


def all_finite(value: np.ndarray) -> bool:
    return bool(np.all(np.isfinite(flatten_numeric(value))))


def symmetric_limits(*arrays: np.ndarray) -> tuple[float, float]:
    max_abs = max(float(np.nanmax(np.abs(array))) for array in arrays if array.size)
    max_abs = max(max_abs, 1e-30)
    return -max_abs, max_abs


def shared_limits(*arrays: np.ndarray, include_zero: bool = False) -> tuple[float, float]:
    low = min(float(np.nanmin(array)) for array in arrays if array.size)
    high = max(float(np.nanmax(array)) for array in arrays if array.size)
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if math.isclose(low, high):
        high = low + 1e-30
    return low, high


def pressure_label() -> str:
    return PRESSURE_LABEL


def nut_ratio_limits(*ratios: np.ndarray) -> tuple[float, float]:
    vmax = max(float(np.nanmax(ratio)) for ratio in ratios if ratio.size)
    return 0.0, max(vmax, 1e-30)


def normalized_pressure_difference(
    p_epsilon: np.ndarray,
    outlet_epsilon: float,
    p_omega_sst: np.ndarray,
    outlet_omega_sst: float,
) -> np.ndarray:
    return (p_omega_sst - outlet_omega_sst) - (p_epsilon - outlet_epsilon)


def reattachment_marker_from_summary(summary: dict[str, Any]) -> float | None:
    value = summary.get("x_reattachment_raw")
    if value in {None, ""}:
        return None
    return float(value)


def public_manifest_has_no_absolute_paths(manifest: dict[str, Any]) -> bool:
    text = json.dumps(manifest, sort_keys=True)
    forbidden = ["D:\\", "/mnt/", "/home/", "C:\\", "\\Users\\"]
    return not any(token in text for token in forbidden)


def is_raw_field_public_path(path: Path) -> bool:
    return path.name in {"C", "U", "p", "k", "nut", "epsilon", "omega", "phi", "V", "yPlus", "wallShearStress"}


def parse_nu(physical_properties: Path) -> float | None:
    text = re.sub(r"/\*.*?\*/", "", physical_properties.read_text(errors="ignore"), flags=re.DOTALL)
    text = re.sub(r"//.*", "", text)
    match = re.search(
        r"(?m)^\s*nu\s+(?:\[[^\]]+\]\s*)?([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*;",
        text,
    )
    return float(match.group(1)) if match else None


def selected_profile(output_root: Path, fallback: str = "conservative_common") -> str:
    selection = output_root / "selected/selection_summary.json"
    if selection.exists():
        return json.loads(selection.read_text(encoding="utf-8")).get("selected_profile", fallback)
    return fallback


def case_dirs(output_root: Path, profile: str) -> dict[str, Path]:
    return {model: output_root / profile / model for model in MODELS}


def read_case_data(case_dir: Path, model: str) -> dict[str, Any]:
    summary = json.loads((case_dir / "results/solver_summary.json").read_text(encoding="utf-8"))
    final_time = str(summary.get("final_time") or tools.latest_numeric_time(case_dir))
    time_dir = case_dir / final_time
    fields: dict[str, Any] = {}
    for field in ["C", "U", "p", "k", "nut"]:
        dimensions, values = read_internal_field(time_dir / field)
        fields[field] = {"dimensions": dimensions, "values": values}
    turbulence_field = "epsilon" if model == "kEpsilon" else "omega"
    dimensions, values = read_internal_field(time_dir / turbulence_field)
    fields[turbulence_field] = {"dimensions": dimensions, "values": values}
    manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    reattachment = json.loads((case_dir / "results/reattachment_summary.json").read_text(encoding="utf-8"))
    pressure_rows = read_csv_rows(case_dir / "results/patch_pressure_summary.csv")
    patch_pressure = pressure_rows[0] if pressure_rows else {}
    geometry = tools.audit_geometry_from_block_mesh_text((case_dir / "system/blockMeshDict").read_text(errors="ignore"))
    return {
        "model": model,
        "case_dir": case_dir,
        "final_time": final_time,
        "summary": summary,
        "manifest": manifest,
        "reattachment": reattachment,
        "patch_pressure": patch_pressure,
        "geometry": geometry,
        "fields": fields,
    }


def field_count(data: dict[str, Any], field: str) -> int:
    values = data["fields"][field]["values"]
    return int(values.shape[0])


def field_visualization_audit(model_data: dict[str, dict[str, Any]], selected: str) -> dict[str, Any]:
    eps = model_data["kEpsilon"]
    sst = model_data["kOmegaSST"]
    c_eps = eps["fields"]["C"]["values"]
    c_sst = sst["fields"]["C"]["values"]
    mesh_hashes = {model: data["manifest"].get("mesh_hash") for model, data in model_data.items()}
    field_checks = {}
    for model, data in model_data.items():
        model_checks = {}
        for field, payload in data["fields"].items():
            model_checks[field] = {
                "count": int(payload["values"].shape[0]),
                "finite": all_finite(payload["values"]),
                "dimensions": payload["dimensions"],
            }
        field_checks[model] = model_checks
    cell_count_consistent = c_eps.shape[0] == c_sst.shape[0]
    coordinates_identical = bool(cell_count_consistent and np.array_equal(c_eps, c_sst))
    mesh_hash_consistent = len(set(mesh_hashes.values())) == 1
    profile_ok = all(
        data["manifest"].get("profile_name") == selected
        or data["summary"].get("profile_name") == selected
        for data in model_data.values()
    )
    status_ok = all(data["summary"].get("status") == "converged" for data in model_data.values())
    quality_ok = all(data["summary"].get("quality_gate_status") == "passed" for data in model_data.values())
    finite_ok = all(check["finite"] for model_checks in field_checks.values() for check in model_checks.values())
    difference_allowed = cell_count_consistent and coordinates_identical and mesh_hash_consistent
    return {
        "selected_profile": selected,
        "cell_count_consistent": cell_count_consistent,
        "coordinates_identical": coordinates_identical,
        "mesh_hash_consistent": mesh_hash_consistent,
        "mesh_hashes": mesh_hashes,
        "profile_ok": profile_ok,
        "status_ok": status_ok,
        "quality_gate_status_ok": quality_ok,
        "all_values_finite": finite_ok,
        "difference_map_allowed": difference_allowed,
        "all_required_checks_passed": difference_allowed and profile_ok and status_ok and quality_ok and finite_ok,
        "field_checks": field_checks,
    }


def triangulation_from_centres(centres: np.ndarray) -> mtri.Triangulation:
    return mtri.Triangulation(centres[:, 0], centres[:, 1])


def add_geometry_markers(ax: plt.Axes, data: dict[str, Any], model: str) -> None:
    step_x = data["geometry"].get("step_x")
    if step_x is not None:
        ax.axvline(float(step_x), color="0.25", linestyle="--", linewidth=0.9)
    x_reattachment = reattachment_marker_from_summary(data["reattachment"])
    if x_reattachment is not None:
        ax.axvline(x_reattachment, color=MODEL_COLORS[model], linestyle="-", linewidth=1.1)


def configure_field_axis(ax: plt.Axes, title: str) -> None:
    ax.set_title(title)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_aspect("equal", adjustable="box")


def plot_velocity_comparison(model_data: dict[str, dict[str, Any]], output: Path) -> None:
    eps = model_data["kEpsilon"]
    sst = model_data["kOmegaSST"]
    tri = triangulation_from_centres(eps["fields"]["C"]["values"])
    ux_eps = eps["fields"]["U"]["values"][:, 0]
    ux_sst = sst["fields"]["U"]["values"][:, 0]
    mag_eps = np.linalg.norm(eps["fields"]["U"]["values"], axis=1)
    mag_sst = np.linalg.norm(sst["fields"]["U"]["values"], axis=1)
    ux_limits = symmetric_limits(ux_eps, ux_sst)
    mag_limits = shared_limits(mag_eps, mag_sst, include_zero=True)
    fig, axes = plt.subplots(2, 2, figsize=(12, 6), constrained_layout=True)
    specs = [
        (axes[0, 0], eps, "kEpsilon", ux_eps, "kEpsilon Ux", ux_limits, "coolwarm", True),
        (axes[0, 1], sst, "kOmegaSST", ux_sst, "kOmegaSST Ux", ux_limits, "coolwarm", True),
        (axes[1, 0], eps, "kEpsilon", mag_eps, "kEpsilon |U|", mag_limits, "viridis", False),
        (axes[1, 1], sst, "kOmegaSST", mag_sst, "kOmegaSST |U|", mag_limits, "viridis", False),
    ]
    contours = []
    for ax, data, model, values, title, limits, cmap, zero_contour in specs:
        contour = ax.tricontourf(tri, values, levels=40, vmin=limits[0], vmax=limits[1], cmap=cmap)
        if zero_contour:
            ax.tricontour(tri, values, levels=[0.0], colors="black", linewidths=0.8)
        add_geometry_markers(ax, data, model)
        configure_field_axis(ax, title)
        contours.append(contour)
    fig.colorbar(contours[0], ax=axes[0, :], label="Ux")
    fig.colorbar(contours[2], ax=axes[1, :], label="|U|")
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure_if_changed(fig, output, dpi=180)
    plt.close(fig)


def plot_pressure_comparison(model_data: dict[str, dict[str, Any]], output: Path) -> None:
    eps = model_data["kEpsilon"]
    sst = model_data["kOmegaSST"]
    tri = triangulation_from_centres(eps["fields"]["C"]["values"])
    p_eps = eps["fields"]["p"]["values"]
    p_sst = sst["fields"]["p"]["values"]
    limits = shared_limits(p_eps, p_sst)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    contours = []
    for ax, data, model, values in [
        (axes[0], eps, "kEpsilon", p_eps),
        (axes[1], sst, "kOmegaSST", p_sst),
    ]:
        contour = ax.tricontourf(tri, values, levels=40, vmin=limits[0], vmax=limits[1], cmap="viridis")
        add_geometry_markers(ax, data, model)
        configure_field_axis(ax, f"{model} p")
        contours.append(contour)
    fig.colorbar(contours[0], ax=axes, label=pressure_label())
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure_if_changed(fig, output, dpi=180)
    plt.close(fig)


def plot_model_difference(model_data: dict[str, dict[str, Any]], output: Path) -> None:
    eps = model_data["kEpsilon"]
    sst = model_data["kOmegaSST"]
    tri = triangulation_from_centres(eps["fields"]["C"]["values"])
    delta_ux = sst["fields"]["U"]["values"][:, 0] - eps["fields"]["U"]["values"][:, 0]
    outlet_eps = float(eps["patch_pressure"]["p_outlet_area_average"])
    outlet_sst = float(sst["patch_pressure"]["p_outlet_area_average"])
    delta_p = normalized_pressure_difference(eps["fields"]["p"]["values"], outlet_eps, sst["fields"]["p"]["values"], outlet_sst)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    for ax, values, title, label in [
        (axes[0], delta_ux, "delta_Ux = kOmegaSST - kEpsilon", "delta_Ux"),
        (
            axes[1],
            delta_p,
            "delta_p_normalized =\n(p - p_outlet)_kOmegaSST -\n(p - p_outlet)_kEpsilon",
            "delta_p_normalized (m^2/s^2)",
        ),
    ]:
        limits = symmetric_limits(values)
        contour = ax.tricontourf(tri, values, levels=40, vmin=limits[0], vmax=limits[1], cmap="coolwarm")
        configure_field_axis(ax, title)
        fig.colorbar(contour, ax=ax, label=label)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure_if_changed(fig, output, dpi=180)
    plt.close(fig)


def plot_turbulent_viscosity_ratio(model_data: dict[str, dict[str, Any]], output: Path) -> dict[str, Any]:
    nu_values = {
        model: parse_nu(data["case_dir"] / "constant/physicalProperties")
        for model, data in model_data.items()
    }
    if any(value is None or value <= 0 for value in nu_values.values()):
        return {"generated": False, "reason": "nu_not_reliably_parsed", "nu_values": nu_values}
    if not math.isclose(nu_values["kEpsilon"], nu_values["kOmegaSST"], rel_tol=0.0, abs_tol=0.0):
        return {"generated": False, "reason": "nu_values_differ", "nu_values": nu_values}
    nu = float(nu_values["kEpsilon"])
    eps = model_data["kEpsilon"]
    sst = model_data["kOmegaSST"]
    tri = triangulation_from_centres(eps["fields"]["C"]["values"])
    ratio_eps = eps["fields"]["nut"]["values"] / nu
    ratio_sst = sst["fields"]["nut"]["values"] / nu
    limits = nut_ratio_limits(ratio_eps, ratio_sst)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    contours = []
    for ax, data, model, ratio in [
        (axes[0], eps, "kEpsilon", ratio_eps),
        (axes[1], sst, "kOmegaSST", ratio_sst),
    ]:
        contour = ax.tricontourf(tri, ratio, levels=40, vmin=limits[0], vmax=limits[1], cmap="magma")
        add_geometry_markers(ax, data, model)
        configure_field_axis(ax, f"{model} nut / nu")
        contours.append(contour)
    fig.colorbar(contours[0], ax=axes, label="nut / nu")
    output.parent.mkdir(parents=True, exist_ok=True)
    save_figure_if_changed(fig, output, dpi=180)
    plt.close(fig)
    return {"generated": True, "nu": nu, "nu_values": nu_values, "path": output.as_posix()}


def is_canonical_diagnostic_output(output_root: Path) -> bool:
    return output_root.name == "stability_continuation"


def copy_public_outputs(output_root: Path, public_results: Path, public_figures: Path, viscosity_result: dict[str, Any]) -> dict[str, Any]:
    final_audit = output_root / "final_audit"
    comparison_figures = output_root / "comparison/figures"
    public_results.mkdir(parents=True, exist_ok=True)
    public_figures.mkdir(parents=True, exist_ok=True)
    if is_canonical_diagnostic_output(output_root):
        result_sources = {
            "diagnostic_model_summary.csv": final_audit / "selected_model_summary.csv",
            "model_summary.csv": final_audit / "selected_model_summary.csv",
            "quality_gates.csv": final_audit / "selected_quality_gates.csv",
            "field_stability.csv": final_audit / "selected_field_stability.csv",
            "reattachment_summary.csv": final_audit / "selected_reattachment.csv",
            "qoi_stability.csv": final_audit / "qoi_stability.csv",
            "wall_shear_stability_summary.csv": final_audit / "sst_wall_shear_stability.csv",
        }
    else:
        result_sources = {
            "model_summary.csv": final_audit / "selected_model_summary.csv",
            "quality_gates.csv": final_audit / "selected_quality_gates.csv",
            "field_stability.csv": final_audit / "selected_field_stability.csv",
            "reattachment_summary.csv": final_audit / "selected_reattachment.csv",
            "relaxation_profile.json": final_audit / "relaxation_profile.json",
        }
    copied_results = []
    for name, src in result_sources.items():
        if not src.exists():
            continue
        dst = public_results / name
        copy_if_changed(src, dst)
        copied_results.append(dst.as_posix())
    figure_names = []
    if not is_canonical_diagnostic_output(output_root):
        figure_names.extend([
            "residual_by_field_comparison.png",
            "normalized_residual_control.png",
        ])
    figure_names.extend([
        "field_velocity_comparison.png",
        "field_pressure_comparison.png",
        "field_model_difference.png",
        "lower_wall_shear_comparison.png",
        "yplus_distribution_comparison.png",
        "pressure_recovery_comparison.png",
        "iterations_and_cost_comparison.png",
    ])
    if viscosity_result.get("generated"):
        figure_names.append("turbulent_viscosity_ratio_comparison.png")
    copied_figures = []
    for name in figure_names:
        src = comparison_figures / name
        if src.exists():
            dst = public_figures / name
            copy_if_changed(src, dst)
            copied_figures.append(dst.as_posix())
    return {"results": copied_results, "figures": copied_figures}


def make_public_manifest(output_root: Path, selected: str, public_results: Path) -> dict[str, Any]:
    qoi_rows = {
        row.get("model"): row
        for row in read_csv_rows(output_root / "final_audit/qoi_stability.csv")
    } if (output_root / "final_audit/qoi_stability.csv").exists() else {}
    rows = []
    for model, case_dir in case_dirs(output_root, selected).items():
        summary = json.loads((case_dir / "results/solver_summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
        qoi = qoi_rows.get(model, {})
        row = {
            "run_id": "rans_pitzdaily_formal_v2",
            "snapshot_id": "canonical_diagnostic_snapshot"
            if is_canonical_diagnostic_output(output_root)
            else "historical_pre_stability_snapshot",
            "model": model,
            "profile": selected,
            "status": summary.get("status"),
            "quality_gate_status": summary.get("quality_gate_status"),
            "final_iteration": summary.get("actual_iterations"),
            "intended_use": "paired RANS diagnostic"
            if is_canonical_diagnostic_output(output_root)
            else "historical pre-stability reference",
            "qoi_stability_passed": qoi.get("qoi_stability_passed"),
            "wall_shear_curve_relative_L2": qoi.get("wall_shear_curve_relative_L2"),
            "openfoam_version": manifest.get("openfoam_version"),
            "git_commit": manifest.get("git_commit"),
            "mesh_hash": manifest.get("mesh_hash"),
            "system_fvSolution_hash": manifest.get("generated_case_hashes", {}).get("system/fvSolution"),
        }
        row["lightweight_hash"] = sha256_bytes(json.dumps(row, sort_keys=True).encode("utf-8"))
        rows.append(row)
    manifest = {
        "run_id": "rans_pitzdaily_formal_v2",
        "selected_profile": selected,
        "snapshot_id": "canonical_diagnostic_snapshot"
        if is_canonical_diagnostic_output(output_root)
        else "historical_pre_stability_snapshot",
        "models": rows,
    }
    if not public_manifest_has_no_absolute_paths(manifest):
        raise ValueError("public manifest contains an absolute local path")
    write_json(public_results / "run_manifest_public.json", manifest)
    return manifest


def write_readme_snippet(output_root: Path) -> None:
    rows = read_csv_rows(output_root / "final_audit/selected_model_summary.csv")
    columns = [
        "model",
        "actual_iterations",
        "pressure_recovery_kinematic",
        "lowerWall_yplus_median",
        "lowerWall_yplus_p95",
        "reattachment_length_normalized",
    ]
    table_lines = [", ".join(columns)]
    for row in rows:
        table_lines.append(
            ", ".join(
                [
                    row.get("model", ""),
                    str(int(float(row.get("actual_iterations", "0")))),
                    f"{float(row.get('pressure_recovery_kinematic', 0.0)):.3f}",
                    f"{float(row.get('lowerWall_yplus_median', 0.0)):.2f}",
                    f"{float(row.get('lowerWall_yplus_p95', 0.0)):.2f}",
                    f"{float(row.get('reattachment_length_normalized', 0.0)):.2f}",
                ]
            )
        )
    qoi_path = output_root / "final_audit/qoi_stability.json"
    if qoi_path.exists():
        qoi = json.loads(qoi_path.read_text(encoding="utf-8"))
        if qoi.get("qoi_stability_passed"):
            stability_status = "passed"
            comparison_status_line = "paired RANS model diagnostic with QoI stability passed."
        else:
            stability_status = "not passed"
            comparison_status_line = "quality_incomplete_comparison; paired diagnostic because at least one QoI stability gate did not pass."
    else:
        stability_status = "not yet audited"
        comparison_status_line = "quality_incomplete_comparison until QoI stability is audited."
    snippet = PUBLIC_SUMMARY_TEXT.format(
        table="\n".join(table_lines),
        selected_profile=selected_profile(output_root),
        stability_status=stability_status,
        comparison_status_line=comparison_status_line,
    )
    (output_root / "final_audit/readme_rans_section.txt").write_text(snippet, encoding="utf-8")


def generate_outputs(output_root: Path, selected: str, public_results: Path, public_figures: Path) -> dict[str, Any]:
    cases = case_dirs(output_root, selected)
    model_data = {model: read_case_data(case_dir, model) for model, case_dir in cases.items()}
    audit = field_visualization_audit(model_data, selected)
    if is_canonical_diagnostic_output(output_root):
        audit["diagnostic_snapshot_allowed"] = (
            audit["difference_map_allowed"]
            and audit["profile_ok"]
            and audit["all_values_finite"]
        )
        audit["all_required_checks_passed"] = audit["diagnostic_snapshot_allowed"]
        audit["diagnostic_status_note"] = (
            "canonical diagnostic snapshot allows field figures for max_iterations_reached/"
            "incomplete_convergence cases when mesh, coordinates, and finite fields pass"
        )
    write_json(output_root / "final_audit/field_visualization_audit.json", audit)
    tools.write_residual_control_figures(cases, output_root / "comparison/figures")
    if not audit["all_required_checks_passed"]:
        return {"audit": audit, "figures": [], "public": {}, "viscosity": {"generated": False, "reason": "audit_failed"}}
    figure_dir = output_root / "comparison/figures"
    velocity = figure_dir / "field_velocity_comparison.png"
    pressure = figure_dir / "field_pressure_comparison.png"
    difference = figure_dir / "field_model_difference.png"
    viscosity = figure_dir / "turbulent_viscosity_ratio_comparison.png"
    plot_velocity_comparison(model_data, velocity)
    plot_pressure_comparison(model_data, pressure)
    plot_model_difference(model_data, difference)
    viscosity_result = plot_turbulent_viscosity_ratio(model_data, viscosity)
    copied = copy_public_outputs(output_root, public_results, public_figures, viscosity_result)
    manifest = make_public_manifest(output_root, selected, public_results)
    write_readme_snippet(output_root)
    return {
        "audit": audit,
        "figures": [
            velocity.as_posix(),
            pressure.as_posix(),
            difference.as_posix(),
            *( [viscosity.as_posix()] if viscosity_result.get("generated") else [] ),
        ],
        "public": copied,
        "public_manifest": manifest,
        "viscosity": viscosity_result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/rans_pitzdaily_formal_v2")
    parser.add_argument("--selected-profile", default=None)
    parser.add_argument("--public-results-dir", type=Path, default=ROOT / "results/public/rans_pitzdaily")
    parser.add_argument("--public-figures-dir", type=Path, default=ROOT / "figures/rans_pitzdaily")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = args.selected_profile or selected_profile(args.output_root)
    result = generate_outputs(args.output_root, selected, args.public_results_dir, args.public_figures_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["audit"]["all_required_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
