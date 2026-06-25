#!/usr/bin/env python3
"""Run the OpenFOAM Re=100 cavity validation-v2 workflow."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from diagnose_cavity_grid import exact_sample_dict, profile_from_xy, relative_l2
from evaluate_cavity_validation import compare_profile, read_two_column_csv
from postprocess_cavity import read_internal_vector_field


RESOLUTIONS = [20, 40, 80, 160]
DEFAULT_END_TIMES = [20, 30, 40, 50, 60, 70, 80]
REFERENCE_U = ROOT / "data/reference/re100_centerline_u.csv"
REFERENCE_V = ROOT / "data/reference/re100_centerline_v.csv"
CASE_DIR = ROOT / "cases/lid_driven_cavity"
STEADY_INTERVAL = 5.0
STEADY_THRESHOLD = 1e-5
COURANT_LIMIT = 0.5
DELTA_T = 0.0025
WRITE_INTERVAL_TIME = 0.5


@dataclass(frozen=True)
class RunResult:
    resolution: int
    status: str
    run_dir: Path
    final_time: float
    quality_gate_passed: bool


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def numeric_time_dirs(case_dir: Path) -> list[tuple[float, Path]]:
    rows: list[tuple[float, Path]] = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            rows.append((float(path.name), path))
        except ValueError:
            continue
    return sorted(rows)


def nearest_time_dir(times: list[tuple[float, Path]], target: float) -> tuple[float, Path]:
    if not times:
        raise ValueError("No numeric OpenFOAM time directories are available.")
    return min(times, key=lambda item: abs(item[0] - target))


def read_velocity(time_dir: Path, resolution: int) -> list[tuple[float, float, float]]:
    return read_internal_vector_field(time_dir / "U", expected_count=resolution * resolution)


def fixed_interval_l2(case_dir: Path, resolution: int, interval: float) -> dict[str, object]:
    times = numeric_time_dirs(case_dir)
    latest_time, latest_dir = times[-1]
    comparison_time, comparison_dir = nearest_time_dir(times, latest_time - interval)
    latest = read_velocity(latest_dir, resolution)
    comparison = read_velocity(comparison_dir, resolution)
    return {
        "latest_time": latest_time,
        "comparison_time": comparison_time,
        "actual_interval": latest_time - comparison_time,
        "relative_l2": relative_l2(latest, comparison),
    }


def parse_icofoam_log(path: Path) -> dict[str, object]:
    times: list[float] = []
    max_co_values: list[float] = []
    mean_co_values: list[float] = []
    for line in path.read_text(errors="ignore").splitlines():
        if line.startswith("Time = "):
            try:
                times.append(float(line.split("=", 1)[1].strip().rstrip("s")))
            except ValueError:
                pass
        if "Courant Number mean:" in line and " max:" in line:
            try:
                prefix, max_part = line.split(" max:", 1)
                mean_co_values.append(float(prefix.rsplit(":", 1)[1].strip()))
                max_co_values.append(float(max_part.strip()))
            except ValueError:
                pass
    deltas = [b - a for a, b in zip(times, times[1:])]
    return {
        "time_steps": len(times),
        "observed_deltaT_min": min(deltas) if deltas else None,
        "observed_deltaT_max": max(deltas) if deltas else None,
        "observed_meanCo_final": mean_co_values[-1] if mean_co_values else None,
        "observed_maxCo_final": max_co_values[-1] if max_co_values else None,
        "observed_maxCo": max(max_co_values) if max_co_values else None,
    }


def read_profile(path: Path) -> list[tuple[float, float]]:
    return sorted(read_two_column_csv(path))


def write_profile_with_actual_coordinates(
    path: Path,
    profile: list[tuple[float, float]],
    *,
    coordinate_name: str,
    component_name: str,
    fixed_axis: str,
    fixed_value: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([coordinate_name, component_name, f"actual_{fixed_axis}"])
        for coordinate, value in profile:
            writer.writerow([coordinate, value, fixed_value])


def sample_exact_centerlines(run_dir: Path, resolution: int) -> tuple[Path, Path]:
    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)
    sample_dict = run_dir / "exactSampleDict"
    sample_dict.write_text(exact_sample_dict(reference_u, reference_v), encoding="utf-8")
    shutil.rmtree(CASE_DIR / "postProcessing", ignore_errors=True)
    log_path = run_dir / "logs/postProcess_exact_sample.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        completed = subprocess.run(
            [
                "postProcess",
                "-case",
                str(CASE_DIR),
                "-dict",
                str(sample_dict.resolve()),
                "-latestTime",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"postProcess exact sample failed for N{resolution}; see {log_path}")
    sample_root = CASE_DIR / "postProcessing/exactCenterlines"
    latest_dir = max(sample_root.iterdir(), key=lambda path: float(path.name))
    vertical = profile_from_xy(latest_dir / "verticalCenterline.xy", coordinate="y", component="Ux")
    horizontal = profile_from_xy(latest_dir / "horizontalCenterline.xy", coordinate="x", component="Uy")
    u_path = run_dir / "results/centerline_u_exact.csv"
    v_path = run_dir / "results/centerline_v_exact.csv"
    write_profile_with_actual_coordinates(
        u_path,
        vertical,
        coordinate_name="y",
        component_name="Ux",
        fixed_axis="x",
        fixed_value=0.5,
    )
    write_profile_with_actual_coordinates(
        v_path,
        horizontal,
        coordinate_name="x",
        component_name="Uy",
        fixed_axis="y",
        fixed_value=0.5,
    )
    return u_path, v_path


def run_cavity_segment(
    resolution: int,
    target_end_time: float,
    run_dir: Path,
    *,
    start_from_latest: bool,
    overwrite: bool,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MESH_RESOLUTION": str(resolution),
            "OUTPUT_ROOT": str(run_dir),
            "END_TIME": f"{target_end_time:g}",
            "DELTA_T": f"{DELTA_T:g}",
            "COURANT_LIMIT": f"{COURANT_LIMIT:g}",
            "WRITE_INTERVAL_TIME": f"{WRITE_INTERVAL_TIME:g}",
            "STEADY_THRESHOLD": f"{STEADY_THRESHOLD:g}",
            "PURGE_WRITE": "0",
            "SAVE_FINAL_FIELDS": "1",
            "SAVE_FINAL_MINUS_INTERVAL": f"{STEADY_INTERVAL:g}",
            "OVERWRITE": "1" if overwrite else "0",
        }
    )
    if start_from_latest:
        env["START_FROM_LATEST"] = "1"
        env["OVERWRITE"] = "1"
    subprocess.run(["bash", str(ROOT / "scripts/run_cavity.sh")], cwd=ROOT, env=env, check=True)
    segment_dir = run_dir / "logs/segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    for name in ["icoFoam.log", "blockMesh.log", "checkMesh.log"]:
        source = run_dir / "logs" / name
        if source.exists():
            shutil.copy2(source, segment_dir / f"{Path(name).stem}_to_{target_end_time:g}.log")


def aggregate_courant(run_dir: Path) -> dict[str, object]:
    logs = sorted((run_dir / "logs/segments").glob("icoFoam_to_*.log"))
    parsed = [parse_icofoam_log(path) for path in logs]
    max_values = [row["observed_maxCo"] for row in parsed if row["observed_maxCo"] is not None]
    min_deltas = [row["observed_deltaT_min"] for row in parsed if row["observed_deltaT_min"] is not None]
    max_deltas = [row["observed_deltaT_max"] for row in parsed if row["observed_deltaT_max"] is not None]
    return {
        "observed_deltaT_min": min(min_deltas) if min_deltas else None,
        "observed_deltaT_max": max(max_deltas) if max_deltas else None,
        "observed_maxCo": max(max_values) if max_values else None,
        "number_of_steps": sum(int(row["time_steps"]) for row in parsed),
    }


def quality_gate_reasons(
    *,
    observed_max_co: float | None,
    steady_relative_l2: float,
    sample_count_u: int,
    sample_count_v: int,
) -> list[str]:
    reasons = []
    if observed_max_co is None or observed_max_co > COURANT_LIMIT:
        reasons.append("courant_gate_failed")
    if sample_count_u != 17 or sample_count_v != 17:
        reasons.append("exact_sample_count_failed")
    if steady_relative_l2 > STEADY_THRESHOLD:
        reasons.append("steady_gate_failed")
    return reasons


def update_run_metadata(
    run_dir: Path,
    *,
    resolution: int,
    status: str,
    steady: dict[str, object],
    sample_count_u: int,
    sample_count_v: int,
    quality_gate_passed: bool,
    quality_reasons: list[str],
) -> dict[str, object]:
    metadata_path = run_dir / "metadata/run_metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    courant = aggregate_courant(run_dir)
    observed_max_co = courant["observed_maxCo"]
    metadata.update(
        {
            "resolution": resolution,
            "status": status,
            "final_time": steady["latest_time"],
            "requested_deltaT": DELTA_T,
            "observed_deltaT_min": courant["observed_deltaT_min"],
            "observed_deltaT_max": courant["observed_deltaT_max"],
            "observed_maxCo": observed_max_co,
            "courant_limit": COURANT_LIMIT,
            "courant_gate_passed": observed_max_co is not None and observed_max_co <= COURANT_LIMIT,
            "number_of_steps": courant["number_of_steps"],
            "steady_interval_physical_time": steady["actual_interval"],
            "steady_relative_L2_over_5": steady["relative_l2"],
            "steady_gate_passed": steady["relative_l2"] <= STEADY_THRESHOLD,
            "sample_count_U": sample_count_u,
            "sample_count_V": sample_count_v,
            "exact_sample_gate_passed": sample_count_u == 17 and sample_count_v == 17,
            "quality_gate_passed": quality_gate_passed,
            "quality_gate_reasons": quality_reasons,
        }
    )
    write_json(metadata_path, metadata)
    write_json(run_dir / "metadata/steady_state_v2.json", steady)
    return metadata


def run_single_resolution(resolution: int, run_root: Path, end_times: list[float]) -> RunResult:
    run_dir = run_root / f"N{resolution}"
    target_index = 0
    status = "failed"
    quality_gate_passed = False
    last_metadata: dict[str, object] | None = None
    for target_end_time in end_times:
        run_cavity_segment(
            resolution,
            target_end_time,
            run_dir,
            start_from_latest=target_index > 0,
            overwrite=target_index == 0,
        )
        u_path, v_path = sample_exact_centerlines(run_dir, resolution)
        u_profile = read_profile(u_path)
        v_profile = read_profile(v_path)
        steady = fixed_interval_l2(CASE_DIR, resolution, STEADY_INTERVAL)
        courant = aggregate_courant(run_dir)
        steady_passed = steady["relative_l2"] <= STEADY_THRESHOLD
        quality_reasons = quality_gate_reasons(
            observed_max_co=courant["observed_maxCo"],
            steady_relative_l2=steady["relative_l2"],
            sample_count_u=len(u_profile),
            sample_count_v=len(v_profile),
        )
        quality_gate_passed = not quality_reasons
        status = "completed" if quality_gate_passed else "quality_gate_failed"
        if not steady_passed and target_end_time < end_times[-1]:
            status = "running_extension"
        elif not steady_passed and target_end_time >= end_times[-1]:
            status = "not_converged"
        last_metadata = update_run_metadata(
            run_dir,
            resolution=resolution,
            status=status,
            steady=steady,
            sample_count_u=len(u_profile),
            sample_count_v=len(v_profile),
            quality_gate_passed=quality_gate_passed,
            quality_reasons=quality_reasons,
        )
        if quality_gate_passed or status in {"quality_gate_failed", "not_converged"} and steady_passed:
            break
        target_index += 1
    final_time = float((last_metadata or {}).get("final_time", end_times[-1]))
    return RunResult(resolution, status, run_dir, final_time, quality_gate_passed)


def metrics_for_run(run_dir: Path) -> dict[str, object]:
    u_profile = read_profile(run_dir / "results/centerline_u_exact.csv")
    v_profile = read_profile(run_dir / "results/centerline_v_exact.csv")
    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)
    u_metrics = compare_profile(u_profile, reference_u)
    v_metrics = compare_profile(v_profile, reference_v)
    return {
        "RMSE_U": u_metrics.rmse,
        "Linf_U": u_metrics.l_inf,
        "RMSE_V": v_metrics.rmse,
        "Linf_V": v_metrics.l_inf,
        "sample_count_U": u_metrics.count,
        "sample_count_V": v_metrics.count,
    }


def build_validation_summary(run_results: list[RunResult], run_root: Path) -> list[dict[str, object]]:
    rows = []
    for result in run_results:
        metadata = json.loads((result.run_dir / "metadata/run_metadata.json").read_text())
        metrics = metrics_for_run(result.run_dir)
        rows.append(
            {
                "resolution": result.resolution,
                "status": metadata["status"],
                "quality_gate_passed": metadata["quality_gate_passed"],
                "included_in_formal_summary": metadata["quality_gate_passed"],
                "final_time": metadata["final_time"],
                "requested_deltaT": metadata["requested_deltaT"],
                "observed_deltaT_min": metadata["observed_deltaT_min"],
                "observed_deltaT_max": metadata["observed_deltaT_max"],
                "observed_maxCo": metadata["observed_maxCo"],
                "steady_relative_L2_over_5": metadata["steady_relative_L2_over_5"],
                "steady_gate_passed": metadata["steady_gate_passed"],
                **metrics,
            }
        )
    write_csv(
        run_root / "validation_summary.csv",
        rows,
        [
            "resolution",
            "status",
            "quality_gate_passed",
            "included_in_formal_summary",
            "final_time",
            "requested_deltaT",
            "observed_deltaT_min",
            "observed_deltaT_max",
            "observed_maxCo",
            "steady_relative_L2_over_5",
            "steady_gate_passed",
            "RMSE_U",
            "Linf_U",
            "RMSE_V",
            "Linf_V",
            "sample_count_U",
            "sample_count_V",
        ],
    )
    return rows


def rms_pair_difference(
    fine: list[tuple[float, float]],
    coarse: list[tuple[float, float]],
) -> float:
    if len(fine) != len(coarse):
        raise ValueError("Profiles must have matching sample counts for self-convergence.")
    errors = [fine_value - coarse_value for (fine_coord, fine_value), (coarse_coord, coarse_value) in zip(fine, coarse) if math.isclose(fine_coord, coarse_coord, abs_tol=1e-12)]
    if len(errors) != len(fine):
        raise ValueError("Profiles must use identical coordinates for self-convergence.")
    return math.sqrt(sum(error * error for error in errors) / len(errors))


def build_self_convergence(run_results: list[RunResult], run_root: Path) -> list[dict[str, object]]:
    profiles = {
        result.resolution: {
            "u": read_profile(result.run_dir / "results/centerline_u_exact.csv"),
            "v": read_profile(result.run_dir / "results/centerline_v_exact.csv"),
        }
        for result in run_results
    }
    quality_all_passed = all(result.quality_gate_passed for result in run_results)
    if not all(resolution in profiles for resolution in RESOLUTIONS):
        rows = [
            {
                "profile": profile_name,
                "d20_40": "",
                "d40_80": "",
                "d80_160": "",
                "p_20_40_80": "",
                "p_40_80_160": "",
                "differences_decrease": False,
                "quality_gates_passed": quality_all_passed,
                "self_convergence_established": False,
                "observed_order_status": "insufficient_grids",
            }
            for profile_name in ["u", "v"]
        ]
        write_csv(
            run_root / "self_convergence.csv",
            rows,
            [
                "profile",
                "d20_40",
                "d40_80",
                "d80_160",
                "p_20_40_80",
                "p_40_80_160",
                "differences_decrease",
                "quality_gates_passed",
                "self_convergence_established",
                "observed_order_status",
            ],
        )
        return rows
    rows = []
    for profile_name in ["u", "v"]:
        d20_40 = rms_pair_difference(profiles[40][profile_name], profiles[20][profile_name])
        d40_80 = rms_pair_difference(profiles[80][profile_name], profiles[40][profile_name])
        d80_160 = rms_pair_difference(profiles[160][profile_name], profiles[80][profile_name])
        p_20_40_80 = math.log2(d20_40 / d40_80) if d40_80 > 0 else math.nan
        p_40_80_160 = math.log2(d40_80 / d80_160) if d80_160 > 0 else math.nan
        decreasing = d20_40 > d40_80 > d80_160
        finite_positive = (
            math.isfinite(p_20_40_80)
            and math.isfinite(p_40_80_160)
            and p_20_40_80 > 0
            and p_40_80_160 > 0
        )
        established = decreasing and finite_positive and quality_all_passed
        rows.append(
            {
                "profile": profile_name,
                "d20_40": d20_40,
                "d40_80": d40_80,
                "d80_160": d80_160,
                "p_20_40_80": p_20_40_80,
                "p_40_80_160": p_40_80_160,
                "differences_decrease": decreasing,
                "quality_gates_passed": quality_all_passed,
                "self_convergence_established": established,
                "observed_order_status": "established" if established else "not_established",
            }
        )
    write_csv(
        run_root / "self_convergence.csv",
        rows,
        [
            "profile",
            "d20_40",
            "d40_80",
            "d80_160",
            "p_20_40_80",
            "p_40_80_160",
            "differences_decrease",
            "quality_gates_passed",
            "self_convergence_established",
            "observed_order_status",
        ],
    )
    return rows


def build_quality_gates(run_results: list[RunResult], run_root: Path) -> list[dict[str, object]]:
    rows = []
    for result in run_results:
        metadata = json.loads((result.run_dir / "metadata/run_metadata.json").read_text())
        rows.append(
            {
                "resolution": result.resolution,
                "status": metadata["status"],
                "courant_gate_passed": metadata["courant_gate_passed"],
                "steady_gate_passed": metadata["steady_gate_passed"],
                "exact_sample_gate_passed": metadata["exact_sample_gate_passed"],
                "quality_gate_passed": metadata["quality_gate_passed"],
                "material_centerline_change": metadata["steady_relative_L2_over_5"],
                "quality_gate_reasons": ";".join(metadata["quality_gate_reasons"]),
            }
        )
    write_csv(
        run_root / "quality_gates.csv",
        rows,
        [
            "resolution",
            "status",
            "courant_gate_passed",
            "steady_gate_passed",
            "exact_sample_gate_passed",
            "quality_gate_passed",
            "material_centerline_change",
            "quality_gate_reasons",
        ],
    )
    return rows


def write_manifest(run_results: list[RunResult], run_root: Path) -> None:
    rows = [
        {
            "resolution": result.resolution,
            "status": result.status,
            "output_root": str(result.run_dir),
        }
        for result in run_results
    ]
    write_csv(run_root / "manifest.csv", rows, ["resolution", "status", "output_root"])


def write_run_metadata(run_root: Path, run_results: list[RunResult], validation_rows: list[dict[str, object]], self_rows: list[dict[str, object]]) -> None:
    write_json(
        run_root / "run_metadata.json",
        {
            "deltaT": DELTA_T,
            "write_interval_physical_time": WRITE_INTERVAL_TIME,
            "steady_interval_physical_time": STEADY_INTERVAL,
            "steady_threshold": STEADY_THRESHOLD,
            "courant_limit": COURANT_LIMIT,
            "resolutions": RESOLUTIONS,
            "run_status": {f"N{result.resolution}": result.status for result in run_results},
            "all_quality_gates_passed": all(row["quality_gate_passed"] for row in validation_rows),
            "self_convergence_established": all(row["self_convergence_established"] for row in self_rows),
        },
    )


def plot_figures(run_root: Path, validation_rows: list[dict[str, object]], self_rows: list[dict[str, object]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = run_root / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    axes[0].plot([value for _, value in reference_u], [coord for coord, _ in reference_u], "ko", label="Ghia")
    axes[1].plot([coord for coord, _ in reference_v], [value for _, value in reference_v], "ko", label="Ghia")
    for result in sorted(validation_rows, key=lambda row: int(row["resolution"])):
        run_dir = run_root / f"N{int(result['resolution'])}"
        u_profile = read_profile(run_dir / "results/centerline_u_exact.csv")
        v_profile = read_profile(run_dir / "results/centerline_v_exact.csv")
        axes[0].plot([value for _, value in u_profile], [coord for coord, _ in u_profile], marker="o", label=f"N{result['resolution']}")
        axes[1].plot([coord for coord, _ in v_profile], [value for _, value in v_profile], marker="o", label=f"N{result['resolution']}")
    axes[0].set_xlabel("Ux at x=0.5")
    axes[0].set_ylabel("y")
    axes[1].set_xlabel("x")
    axes[1].set_ylabel("Uy at y=0.5")
    for ax in axes:
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "exact_centerline_vs_ghia.png", dpi=160)
    plt.close(fig)

    resolutions = [int(row["resolution"]) for row in validation_rows]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(resolutions, [float(row["RMSE_U"]) for row in validation_rows], marker="o", label="Ux")
    ax.plot(resolutions, [float(row["RMSE_V"]) for row in validation_rows], marker="o", label="Uy")
    ax.set_xlabel("grid resolution N")
    ax.set_ylabel("RMSE vs Ghia")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "reference_rmse_by_grid.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    pairs = ["20-40", "40-80", "80-160"]
    plotted = False
    for row in self_rows:
        if row["d20_40"] == "":
            continue
        ax.plot(pairs, [float(row["d20_40"]), float(row["d40_80"]), float(row["d80_160"])], marker="o", label=row["profile"])
        plotted = True
    if plotted:
        ax.set_ylabel("RMS grid-to-grid difference")
        ax.grid(True, alpha=0.3)
        ax.legend()
    else:
        ax.text(0.5, 0.5, "insufficient grids", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(figures / "grid_to_grid_difference.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    order_labels = ["p20_40_80", "p40_80_160"]
    width = 0.35
    plotted = False
    for offset, row in zip([-width / 2, width / 2], self_rows):
        if row["p_20_40_80"] == "":
            continue
        xs = [0 + offset, 1 + offset]
        ax.bar(xs, [float(row["p_20_40_80"]), float(row["p_40_80_160"])], width=width, label=row["profile"])
        plotted = True
    if plotted:
        ax.set_xticks([0, 1], order_labels)
        ax.set_ylabel("observed order")
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend()
    else:
        ax.text(0.5, 0.5, "insufficient grids", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(figures / "observed_order.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    costs = [int(row["resolution"]) ** 2 * float(row["final_time"]) / DELTA_T for row in validation_rows]
    ax.loglog(costs, [float(row["RMSE_U"]) + float(row["RMSE_V"]) for row in validation_rows], marker="o")
    for row, cost in zip(validation_rows, costs):
        ax.annotate(f"N{row['resolution']}", (cost, float(row["RMSE_U"]) + float(row["RMSE_V"])))
    ax.set_xlabel("cell-steps")
    ax.set_ylabel("RMSE_U + RMSE_V")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(figures / "error_vs_cost.png", dpi=160)
    plt.close(fig)


def run_validation(args: argparse.Namespace) -> int:
    run_root = args.run_root
    if run_root.exists() and any(run_root.iterdir()):
        if not args.overwrite:
            raise SystemExit(f"{run_root} is not empty. Use --overwrite to replace validation-v2 outputs.")
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)

    run_results: list[RunResult] = []
    for resolution in args.resolutions:
        result = run_single_resolution(resolution, run_root, args.end_times)
        run_results.append(result)
        write_manifest(run_results, run_root)

    validation_rows = build_validation_summary(run_results, run_root)
    quality_rows = build_quality_gates(run_results, run_root)
    self_rows = build_self_convergence(run_results, run_root)
    write_manifest(run_results, run_root)
    write_run_metadata(run_root, run_results, validation_rows, self_rows)
    plot_figures(run_root, validation_rows, self_rows)

    print(f"Wrote validation-v2 outputs under {run_root}")
    print("Quality gates:")
    for row in quality_rows:
        print(
            f"  N{row['resolution']}: quality={row['quality_gate_passed']} "
            f"courant={row['courant_gate_passed']} steady={row['steady_gate_passed']} "
            f"sample={row['exact_sample_gate_passed']}"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs/cavity_validation_v2")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resolutions", type=int, nargs="+", default=RESOLUTIONS)
    parser.add_argument("--end-times", type=float, nargs="+", default=DEFAULT_END_TIMES)
    return parser.parse_args()


def main() -> int:
    return run_validation(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
