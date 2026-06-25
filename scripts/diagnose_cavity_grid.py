#!/usr/bin/env python3
"""Diagnose non-monotonic Re=100 cavity grid errors without changing public summaries."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from evaluate_cavity_validation import compare_profile, interpolate_profile, read_two_column_csv
from postprocess_cavity import VectorSample, extract_nearest_centerline, read_internal_vector_field


RESOLUTIONS = [20, 40, 80, 160]
REFERENCE_U = ROOT / "data/reference/re100_centerline_u.csv"
REFERENCE_V = ROOT / "data/reference/re100_centerline_v.csv"


@dataclass(frozen=True)
class ProfileMetrics:
    rmse_u: float
    linf_u: float
    rmse_v: float
    linf_v: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_profile(path: Path) -> list[tuple[float, float]]:
    return read_two_column_csv(path)


def profile_from_xy(path: Path, *, coordinate: str, component: str) -> list[tuple[float, float]]:
    index = {"x": 0, "y": 1, "z": 2, "Ux": 3, "Uy": 4, "Uz": 5}
    rows: list[tuple[float, float]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [float(item) for item in line.split()]
        rows.append((parts[index[coordinate]], parts[index[component]]))
    return rows


def copy_profile(path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, output_path)


def freeze_hashes(runs_root: Path, diagnostics_dir: Path) -> dict[str, object]:
    payload: dict[str, object] = {"runs": {}, "public": {}, "reference": {}}
    for resolution in RESOLUTIONS:
        run_dir = runs_root / f"N{resolution}"
        run_payload: dict[str, object] = {}
        field_candidates = {
            "final_U": [
                (run_dir / "final_fields" / "U", "original_run_final_fields"),
                (
                    diagnostics_dir / "exact_runs" / f"N{resolution}" / "final_fields" / "U",
                    "diagnostic_exact_rerun_matching_original_parameters",
                ),
            ],
            "final_p": [
                (run_dir / "final_fields" / "p", "original_run_final_fields"),
                (
                    diagnostics_dir / "exact_runs" / f"N{resolution}" / "final_fields" / "p",
                    "diagnostic_exact_rerun_matching_original_parameters",
                ),
            ],
        }
        for label, candidates in field_candidates.items():
            for path, source in candidates:
                if path.exists():
                    run_payload[label] = {
                        "path": str(path),
                        "sha256": sha256_file(path),
                        "source": source,
                    }
                    break
            else:
                run_payload[label] = {
                    "path": str(candidates[0][0]),
                    "status": "missing",
                    "source": "not_retained_in_original_run",
                }
        for label, path in {
            "centerline_u": run_dir / "results" / "centerline_u.csv",
            "centerline_v": run_dir / "results" / "centerline_v.csv",
            "solver_log": run_dir / "logs" / "icoFoam.log",
        }.items():
            run_payload[label] = (
                {"path": str(path), "sha256": sha256_file(path)}
                if path.exists()
                else {"path": str(path), "status": "missing"}
            )
        payload["runs"][f"N{resolution}"] = run_payload
    for label, path in {
        "cavity_grid_summary": ROOT / "results/public/cavity_grid_summary.csv",
        "reference_u": REFERENCE_U,
        "reference_v": REFERENCE_V,
    }.items():
        target = payload["public"] if label.startswith("cavity") else payload["reference"]
        target[label] = {"path": str(path), "sha256": sha256_file(path)}
    write_json(diagnostics_dir / "original_result_hashes.json", payload)
    return payload


def audit_centerline_sampling(runs_root: Path, diagnostics_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for resolution in RESOLUTIONS:
        run_dir = runs_root / f"N{resolution}"
        n = resolution
        dx = dy = 1.0 / n
        actual_coord = 0.5 - 0.5 / n
        for profile_name, path, fixed_axis, varying_axis in [
            ("vertical_u", run_dir / "results/centerline_u.csv", "x", "y"),
            ("horizontal_v", run_dir / "results/centerline_v.csv", "y", "x"),
        ]:
            profile = read_profile(path)
            coords = [item[0] for item in profile]
            rows.append(
                {
                    "resolution": resolution,
                    "cell_count": n * n,
                    "dx": dx,
                    "dy": dy,
                    "profile": profile_name,
                    "fixed_axis": fixed_axis,
                    "varying_axis": varying_axis,
                    "actual_line_coordinate": actual_coord,
                    "offset_from_0p5": actual_coord - 0.5,
                    "sample_count": len(profile),
                    "extraction_method": "nearest-cell",
                    "has_duplicate_coordinates": len(set(coords)) != len(coords),
                    "is_sorted": coords == sorted(coords),
                }
            )
            copy_profile(
                path,
                diagnostics_dir
                / f"N{resolution}"
                / path.name.replace(".csv", "_nearest_cell.csv"),
            )
    write_csv(
        diagnostics_dir / "centerline_sampling_audit.csv",
        rows,
        [
            "resolution",
            "cell_count",
            "dx",
            "dy",
            "profile",
            "fixed_axis",
            "varying_axis",
            "actual_line_coordinate",
            "offset_from_0p5",
            "sample_count",
            "extraction_method",
            "has_duplicate_coordinates",
            "is_sorted",
        ],
    )
    return rows


def parse_courant_log(path: Path) -> dict[str, object]:
    time_values: list[float] = []
    mean_co: list[float] = []
    max_co: list[float] = []
    for line in path.read_text(errors="ignore").splitlines():
        if match := re.match(r"Time = ([0-9.+\-eE]+)s?", line.strip()):
            time_values.append(float(match.group(1)))
        if match := re.search(r"Courant Number mean: ([0-9.+\-eE]+) max: ([0-9.+\-eE]+)", line):
            mean_co.append(float(match.group(1)))
            max_co.append(float(match.group(2)))
    deltas = [b - a for a, b in zip(time_values, time_values[1:])]
    return {
        "deltaT_min": min(deltas) if deltas else "",
        "deltaT_max": max(deltas) if deltas else "",
        "mean_Co_final": mean_co[-1] if mean_co else "",
        "max_Co_final": max_co[-1] if max_co else "",
        "max_Co_max": max(max_co) if max_co else "",
        "max_Co_exceeded_0p5": bool(max_co and max(max_co) > 0.5),
        "time_steps_in_log": len(time_values),
    }


def write_time_step_courant_audit(runs_root: Path, diagnostics_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for resolution in RESOLUTIONS:
        run_dir = runs_root / f"N{resolution}"
        parsed = parse_courant_log(run_dir / "logs/icoFoam.log")
        metadata = json.loads((run_dir / "metadata/run_metadata.json").read_text())
        rows.append(
            {
                "resolution": resolution,
                "adjustTimeStep_configured": True,
                "target_maxCo": 0.5,
                "metadata_number_of_steps": metadata.get("number_of_steps", ""),
                **parsed,
            }
        )
    fieldnames = [
        "resolution",
        "adjustTimeStep_configured",
        "target_maxCo",
        "deltaT_min",
        "deltaT_max",
        "mean_Co_final",
        "max_Co_final",
        "max_Co_max",
        "max_Co_exceeded_0p5",
        "time_steps_in_log",
        "metadata_number_of_steps",
    ]
    write_csv(diagnostics_dir / "time_step_courant_audit.csv", rows, fieldnames)
    return rows


def write_version_consistency_audit(runs_root: Path, diagnostics_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for resolution in RESOLUTIONS:
        metadata_path = runs_root / f"N{resolution}" / "metadata/run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        rows.append(
            {
                "context": "original_grid_run",
                "resolution": resolution,
                "metadata_path": str(metadata_path),
                "openfoam_version": metadata.get("openfoam_version", ""),
                "version_status": "OpenFOAM-10" if metadata.get("openfoam_version") == "10" else "check",
            }
        )
    public_summary = ROOT / "results/public/cavity_grid_summary.csv"
    rows.append(
        {
            "context": "public_grid_summary",
            "resolution": "",
            "metadata_path": str(public_summary),
            "openfoam_version": "",
            "version_status": "no_version_column_public_csv_left_unmodified",
        }
    )
    write_csv(
        diagnostics_dir / "version_consistency_audit.csv",
        rows,
        ["context", "resolution", "metadata_path", "openfoam_version", "version_status"],
    )
    return rows


def numeric_time_dirs(case_dir: Path) -> list[tuple[float, Path]]:
    times = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            times.append((float(path.name), path))
        except ValueError:
            continue
    return sorted(times)


def relative_l2(latest: list[tuple[float, float, float]], previous: list[tuple[float, float, float]]) -> float:
    diff_sq = 0.0
    latest_sq = 0.0
    for latest_vec, previous_vec in zip(latest, previous):
        for latest_value, previous_value in zip(latest_vec, previous_vec):
            diff_sq += (latest_value - previous_value) ** 2
            latest_sq += latest_value**2
    return math.sqrt(diff_sq) / max(math.sqrt(latest_sq), 1e-12)


def structured_cell_centres_for_resolution(resolution: int) -> list[tuple[float, float, float]]:
    dx = 1.0 / resolution
    dy = 1.0 / resolution
    return [
        ((i + 0.5) * dx, (j + 0.5) * dy, 0.005)
        for j in range(resolution)
        for i in range(resolution)
    ]


def read_velocity_at_time(time_dir: Path, resolution: int | None = None) -> list[tuple[float, float, float]]:
    expected_count = resolution * resolution if resolution is not None else None
    return read_internal_vector_field(time_dir / "U", expected_count=expected_count)


def nearest_time_dir(
    times: list[tuple[float, Path]],
    target_time: float,
) -> tuple[float, Path]:
    return min(times, key=lambda item: abs(item[0] - target_time))


def fixed_interval_l2(
    case_dir: Path,
    target_interval: float = 5.0,
    *,
    resolution: int | None = None,
    latest_time: float | None = None,
) -> dict[str, object]:
    times = numeric_time_dirs(case_dir)
    if len(times) < 2:
        return {"status": "unavailable"}
    if latest_time is None:
        latest_time, latest_dir = times[-1]
    else:
        latest_time, latest_dir = nearest_time_dir(times, latest_time)
    target_time = latest_time - target_interval
    previous_time, previous_dir = nearest_time_dir(times, target_time)
    latest = read_velocity_at_time(latest_dir, resolution)
    previous = read_velocity_at_time(previous_dir, resolution)
    return {
        "status": "available",
        "latest_time": latest_time,
        "comparison_time": previous_time,
        "actual_interval": latest_time - previous_time,
        "relative_L2_change_over_5_time_units": relative_l2(latest, previous),
    }


def last_write_relative_l2_changes(
    case_dir: Path,
    resolution: int,
    *,
    latest_time: float | None = None,
    change_count: int = 5,
) -> str:
    times = numeric_time_dirs(case_dir)
    if latest_time is not None:
        latest_time, _ = nearest_time_dir(times, latest_time)
        times = [(time_value, path) for time_value, path in times if time_value <= latest_time + 1e-9]
    if len(times) < 2:
        return "unavailable"
    window = times[-(change_count + 1) :]
    changes = []
    for (previous_time, previous_dir), (current_time, current_dir) in zip(window, window[1:]):
        latest = read_velocity_at_time(current_dir, resolution)
        previous = read_velocity_at_time(previous_dir, resolution)
        changes.append(f"{previous_time:g}->{current_time:g}:{relative_l2(latest, previous):.12g}")
    return ";".join(changes)


def nearest_centerline_profiles_at_time(
    case_dir: Path,
    resolution: int,
    time_value: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    times = numeric_time_dirs(case_dir)
    actual_time, time_dir = nearest_time_dir(times, time_value)
    if abs(actual_time - time_value) > 1e-9:
        raise ValueError(f"Requested time {time_value} but nearest available time is {actual_time}")
    centres = structured_cell_centres_for_resolution(resolution)
    velocities = read_velocity_at_time(time_dir, resolution)
    cells = [
        VectorSample(x, y, z, ux, uy, uz)
        for (x, y, z), (ux, uy, uz) in zip(centres, velocities)
    ]
    vertical = extract_nearest_centerline(
        cells,
        fixed_axis="x",
        fixed_value=0.5,
        varying_axis="y",
    )
    horizontal = extract_nearest_centerline(
        cells,
        fixed_axis="y",
        fixed_value=0.5,
        varying_axis="x",
    )
    return (
        [(row.y, row.Ux) for row in vertical],
        [(row.x, row.Uy) for row in horizontal],
    )


def centerline_change_last_fraction(
    case_dir: Path,
    resolution: int,
    *,
    latest_time: float | None = None,
    fraction: float = 0.2,
) -> str:
    times = numeric_time_dirs(case_dir)
    if len(times) < 2:
        return "unavailable"
    if latest_time is None:
        latest_time, _ = times[-1]
    else:
        latest_time, _ = nearest_time_dir(times, latest_time)
    first_time = times[0][0]
    target_time = latest_time - fraction * (latest_time - first_time)
    comparison_time, _ = nearest_time_dir(times, target_time)
    latest_u, latest_v = nearest_centerline_profiles_at_time(case_dir, resolution, latest_time)
    previous_u, previous_v = nearest_centerline_profiles_at_time(case_dir, resolution, comparison_time)
    return (
        f"{comparison_time:g}->{latest_time:g}:"
        f"u_rms={rms_difference(latest_u, previous_u):.12g};"
        f"v_rms={rms_difference(latest_v, previous_v):.12g}"
    )


def diagnostic_steady_row_from_case(
    resolution: int,
    phase: str,
    output_root: Path,
    case_dir: Path,
    *,
    latest_time: float | None = None,
) -> dict[str, object]:
    times = numeric_time_dirs(case_dir)
    if latest_time is None:
        latest_time, latest_dir = times[-1]
    else:
        latest_time, latest_dir = nearest_time_dir(times, latest_time)
    prior_times = [(time_value, path) for time_value, path in times if time_value < latest_time - 1e-9]
    previous_time, previous_dir = prior_times[-1]
    latest = read_velocity_at_time(latest_dir, resolution)
    previous = read_velocity_at_time(previous_dir, resolution)
    fixed = fixed_interval_l2(case_dir, resolution=resolution, latest_time=latest_time)
    solver_summary = json.loads((output_root / "results/solver_summary.json").read_text())
    return {
        "resolution": resolution,
        "phase": phase,
        "latest_time": latest_time,
        "previous_time": previous_time,
        "write_interval_time": latest_time - previous_time,
        "relative_L2_change": relative_l2(latest, previous),
        "last_5_write_relative_L2_changes": last_write_relative_l2_changes(
            case_dir,
            resolution,
            latest_time=latest_time,
        ),
        "final_residual": max(
            item["final_residual"] for item in solver_summary["final_residuals"].values()
        ),
        "continuity_error": (solver_summary["final_continuity_error"] or {}).get("cumulative", ""),
        "centerline_change_last_20_percent": centerline_change_last_fraction(
            case_dir,
            resolution,
            latest_time=latest_time,
        ),
        "fixed_5_status": fixed.get("status"),
        "fixed_5_actual_interval": fixed.get("actual_interval", ""),
        "relative_L2_change_over_5_time_units": fixed.get(
            "relative_L2_change_over_5_time_units",
            "",
        ),
    }


def write_original_steady_audit(runs_root: Path, diagnostics_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for resolution in RESOLUTIONS:
        run_dir = runs_root / f"N{resolution}"
        steady = json.loads((run_dir / "metadata/steady_state.json").read_text())
        solver = json.loads((run_dir / "results/solver_summary.json").read_text())
        latest = float(steady["latest_time"])
        previous = float(steady["previous_time"])
        rows.append(
            {
                "resolution": resolution,
                "phase": "original_runs",
                "latest_time": latest,
                "previous_time": previous,
                "write_interval_time": latest - previous,
                "relative_L2_change": steady["relative_L2_change"],
                "last_5_write_relative_L2_changes": "unavailable_original_fields_not_retained",
                "final_residual": max(
                    item["final_residual"] for item in solver["final_residuals"].values()
                ),
                "continuity_error": (solver["final_continuity_error"] or {}).get("cumulative", ""),
                "centerline_change_last_20_percent": "unavailable_original_fields_not_retained",
                "fixed_5_status": "unavailable_original_fields_not_retained",
                "fixed_5_actual_interval": "",
                "relative_L2_change_over_5_time_units": "",
            }
        )
    return rows


def exact_sample_dict(reference_u: list[tuple[float, float]], reference_v: list[tuple[float, float]]) -> str:
    vertical_points = "\n".join(f"                (0.5 {coord:.10g} 0)" for coord, _ in reference_u)
    horizontal_points = "\n".join(f"                ({coord:.10g} 0.5 0)" for coord, _ in reference_v)
    return f"""FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    object exactSampleDict;
}}
functions
{{
    exactCenterlines
    {{
        type sets;
        libs ("libsampling.so");
        writeControl writeTime;
        interpolationScheme cellPoint;
        setFormat raw;
        fields (U);
        sets
        (
            verticalCenterline
            {{
                type points;
                axis xyz;
                ordered false;
                points
                (
{vertical_points}
                );
            }}
            horizontalCenterline
            {{
                type points;
                axis xyz;
                ordered false;
                points
                (
{horizontal_points}
                );
            }}
        );
    }}
}}
"""


def run_command(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with {completed.returncode}: {' '.join(command)}")


def run_case(
    resolution: int,
    end_time: float,
    output_root: Path,
    *,
    start_from_latest: bool = False,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MESH_RESOLUTION": str(resolution),
            "OUTPUT_ROOT": str(output_root),
            "END_TIME": str(end_time),
            "MAX_CO": "0.5",
            "OVERWRITE": "1",
            "PURGE_WRITE": "0",
        }
    )
    if start_from_latest:
        env["START_FROM_LATEST"] = "1"
    subprocess.run(["bash", "scripts/run_cavity.sh"], cwd=ROOT, env=env, check=True)


def sample_current_case(diagnostics_dir: Path, resolution: int, phase: str) -> tuple[Path, Path]:
    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)
    sample_dict = diagnostics_dir / f"exactSampleDict_N{resolution}_{phase}.dict"
    sample_dict.write_text(exact_sample_dict(reference_u, reference_v), encoding="utf-8")
    case_dir = ROOT / "cases/lid_driven_cavity"
    shutil.rmtree(case_dir / "postProcessing", ignore_errors=True)
    run_command(
        [
            "postProcess",
            "-case",
            str(case_dir),
            "-dict",
            str(sample_dict.resolve()),
            "-latestTime",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        log_path=diagnostics_dir / f"postProcess_exact_sample_N{resolution}_{phase}.log",
    )
    sample_root = case_dir / "postProcessing/exactCenterlines"
    latest_dir = max(sample_root.iterdir(), key=lambda path: float(path.name))
    out_dir = diagnostics_dir / f"N{resolution}" / phase
    out_dir.mkdir(parents=True, exist_ok=True)
    vertical_xy = latest_dir / "verticalCenterline.xy"
    horizontal_xy = latest_dir / "horizontalCenterline.xy"
    vertical_csv = out_dir / "centerline_u_exact_sample.csv"
    horizontal_csv = out_dir / "centerline_v_exact_sample.csv"
    write_profile_csv(vertical_csv, profile_from_xy(vertical_xy, coordinate="y", component="Ux"), "y", "Ux")
    write_profile_csv(horizontal_csv, profile_from_xy(horizontal_xy, coordinate="x", component="Uy"), "x", "Uy")
    return vertical_csv, horizontal_csv


def write_profile_csv(path: Path, profile: list[tuple[float, float]], coordinate: str, component: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([coordinate, component])
        writer.writerows(profile)


def profile_table_rows(
    resolution: int,
    phase: str,
    profile: list[tuple[float, float]],
    coordinate_name: str,
    component_name: str,
) -> list[dict[str, object]]:
    return [
        {
            "resolution": resolution,
            "phase": phase,
            coordinate_name: coordinate,
            component_name: value,
        }
        for coordinate, value in profile
    ]


def write_centerline_profile_table(
    path: Path,
    rows: list[dict[str, object]],
    coordinate_name: str,
    component_name: str,
) -> None:
    write_csv(path, rows, ["resolution", "phase", coordinate_name, component_name])


def profile_metrics(u_profile: list[tuple[float, float]], v_profile: list[tuple[float, float]]) -> ProfileMetrics:
    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)
    u_metrics = compare_profile(u_profile, reference_u)
    v_metrics = compare_profile(v_profile, reference_v)
    return ProfileMetrics(u_metrics.rmse, u_metrics.l_inf, v_metrics.rmse, v_metrics.l_inf)


def pointwise_errors(
    resolution: int,
    method: str,
    profile_name: str,
    cfd_profile: list[tuple[float, float]],
    reference_profile: list[tuple[float, float]],
) -> list[dict[str, object]]:
    rows = []
    for coord, reference_value in reference_profile:
        try:
            cfd_value = interpolate_profile(cfd_profile, coord)
        except ValueError:
            continue
        rows.append(
            {
                "resolution": resolution,
                "sampling_method": method,
                "profile": profile_name,
                "coordinate": coord,
                "cfd_value": cfd_value,
                "reference_value": reference_value,
                "error": cfd_value - reference_value,
            }
        )
    return rows


def plot_pointwise_errors(rows: list[dict[str, object]], diagnostics_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for profile, output_name, x_label, y_label in [
        ("u", "Ux_error_vs_y_by_grid.png", "y", "Ux error"),
        ("v", "Uy_error_vs_x_by_grid.png", "x", "Uy error"),
    ]:
        fig, ax = plt.subplots(figsize=(6, 4))
        for resolution in RESOLUTIONS:
            data = [
                row
                for row in rows
                if row["profile"] == profile
                and row["sampling_method"] == "exact_sample_original"
                and int(row["resolution"]) == resolution
            ]
            if not data:
                continue
            ax.plot(
                [float(row["coordinate"]) for row in data],
                [float(row["error"]) for row in data],
                marker="o",
                label=f"N{resolution}",
            )
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(diagnostics_dir / output_name, dpi=160)
        plt.close(fig)


def rms_difference(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    values = []
    for coord, value in a:
        try:
            other = interpolate_profile(b, coord)
        except ValueError:
            continue
        values.append(value - other)
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else math.nan


def make_self_convergence(exact_profiles: dict[int, tuple[list[tuple[float, float]], list[tuple[float, float]]]], diagnostics_dir: Path) -> tuple[list[dict[str, object]], bool]:
    rows = []
    diffs_u = []
    diffs_v = []
    for coarse, fine in [(20, 40), (40, 80), (80, 160)]:
        u_diff = rms_difference(exact_profiles[fine][0], exact_profiles[coarse][0])
        v_diff = rms_difference(exact_profiles[fine][1], exact_profiles[coarse][1])
        diffs_u.append(u_diff)
        diffs_v.append(v_diff)
        rows.append(
            {
                "grid_pair": f"N{fine}-N{coarse}",
                "profile": "u",
                "rms_difference": u_diff,
                "observed_order_status": "",
            }
        )
        rows.append(
            {
                "grid_pair": f"N{fine}-N{coarse}",
                "profile": "v",
                "rms_difference": v_diff,
                "observed_order_status": "",
            }
        )
    established = all(a > b for a, b in zip(diffs_u, diffs_u[1:])) and all(
        a > b for a, b in zip(diffs_v, diffs_v[1:])
    )
    status = "established" if established else "not_established"
    for row in rows:
        row["observed_order_status"] = status
    write_csv(
        diagnostics_dir / "self_convergence.csv",
        rows,
        ["grid_pair", "profile", "rms_difference", "observed_order_status"],
    )
    return rows, established


def run_diagnostics(runs_root: Path, diagnostics_dir: Path, *, run_openfoam: bool) -> None:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    freeze_hashes(runs_root, diagnostics_dir)
    sampling_rows = audit_centerline_sampling(runs_root, diagnostics_dir)
    courant_rows = write_time_step_courant_audit(runs_root, diagnostics_dir)
    version_rows = write_version_consistency_audit(runs_root, diagnostics_dir)
    steady_rows = write_original_steady_audit(runs_root, diagnostics_dir)

    error_rows: list[dict[str, object]] = []
    pointwise_rows: list[dict[str, object]] = []
    exact_profiles: dict[int, tuple[list[tuple[float, float]], list[tuple[float, float]]]] = {}
    extended_rows: list[dict[str, object]] = []
    nearest_u_rows: list[dict[str, object]] = []
    nearest_v_rows: list[dict[str, object]] = []
    exact_u_rows: list[dict[str, object]] = []
    exact_v_rows: list[dict[str, object]] = []

    reference_u = read_profile(REFERENCE_U)
    reference_v = read_profile(REFERENCE_V)

    final_times = {
        int(row["resolution"]): float(row["final_time"])
        for row in csv.DictReader((ROOT / "results/public/cavity_grid_summary.csv").open())
    }
    original_metrics: dict[int, ProfileMetrics] = {}

    for resolution in RESOLUTIONS:
        run_dir = runs_root / f"N{resolution}"
        nearest_u = read_profile(run_dir / "results/centerline_u.csv")
        nearest_v = read_profile(run_dir / "results/centerline_v.csv")
        nearest_u_rows.extend(profile_table_rows(resolution, "original", nearest_u, "y", "Ux"))
        nearest_v_rows.extend(profile_table_rows(resolution, "original", nearest_v, "x", "Uy"))
        metrics = profile_metrics(nearest_u, nearest_v)
        original_metrics[resolution] = metrics
        error_rows.append(
            {
                "resolution": resolution,
                "sampling_method": "nearest_cell_original",
                "actual_line_coordinate": f"x/y={0.5 - 0.5 / resolution}",
                "RMSE_U": metrics.rmse_u,
                "Linf_U": metrics.linf_u,
                "RMSE_V": metrics.rmse_v,
                "Linf_V": metrics.linf_v,
            }
        )
        pointwise_rows.extend(pointwise_errors(resolution, "nearest_cell_original", "u", nearest_u, reference_u))
        pointwise_rows.extend(pointwise_errors(resolution, "nearest_cell_original", "v", nearest_v, reference_v))

    if run_openfoam:
        for resolution in [20, 40, 80]:
            run_case(
                resolution,
                final_times[resolution],
                diagnostics_dir / "exact_runs" / f"N{resolution}",
            )
            u_csv, v_csv = sample_current_case(diagnostics_dir, resolution, "exact_original")
            exact_profiles[resolution] = (read_profile(u_csv), read_profile(v_csv))
            steady_rows.append(
                diagnostic_steady_row_from_case(
                    resolution,
                    "diagnostic_original_rerun",
                    diagnostics_dir / "exact_runs" / f"N{resolution}",
                    ROOT / "cases/lid_driven_cavity",
                    latest_time=final_times[resolution],
                )
            )
            if resolution == 80:
                before_u, before_v = exact_profiles[resolution]
                before_metrics = profile_metrics(before_u, before_v)
                run_case(
                    80,
                    50,
                    diagnostics_dir / "extended_runs" / "N80",
                    start_from_latest=True,
                )
                u_ext, v_ext = sample_current_case(diagnostics_dir, 80, "extended_t50")
                after_u, after_v = read_profile(u_ext), read_profile(v_ext)
                after_metrics = profile_metrics(after_u, after_v)
                fixed_ext = fixed_interval_l2(
                    ROOT / "cases/lid_driven_cavity",
                    resolution=80,
                    latest_time=50,
                )
                extended_rows.append(extension_row(80, before_u, before_v, before_metrics, after_u, after_v, after_metrics, fixed_ext))
                steady_rows.append(
                    diagnostic_steady_row_from_case(
                        80,
                        "diagnostic_extended_t50",
                        diagnostics_dir / "extended_runs" / "N80",
                        ROOT / "cases/lid_driven_cavity",
                        latest_time=50,
                    )
                )

        run_case(160, final_times[160], diagnostics_dir / "exact_runs" / "N160")
        u_csv, v_csv = sample_current_case(diagnostics_dir, 160, "exact_original")
        exact_profiles[160] = (read_profile(u_csv), read_profile(v_csv))
        steady_rows.append(
            diagnostic_steady_row_from_case(
                160,
                "diagnostic_original_rerun",
                diagnostics_dir / "exact_runs" / "N160",
                ROOT / "cases/lid_driven_cavity",
                latest_time=final_times[160],
            )
        )
        before_u, before_v = exact_profiles[160]
        before_metrics = profile_metrics(before_u, before_v)
        run_case(160, 60, diagnostics_dir / "extended_runs" / "N160", start_from_latest=True)
        u_ext, v_ext = sample_current_case(diagnostics_dir, 160, "extended_t60")
        after_u, after_v = read_profile(u_ext), read_profile(v_ext)
        after_metrics = profile_metrics(after_u, after_v)
        fixed_ext = fixed_interval_l2(
            ROOT / "cases/lid_driven_cavity",
            resolution=160,
            latest_time=60,
        )
        extended_rows.append(extension_row(160, before_u, before_v, before_metrics, after_u, after_v, after_metrics, fixed_ext))
        steady_rows.append(
            diagnostic_steady_row_from_case(
                160,
                "diagnostic_extended_t60",
                diagnostics_dir / "extended_runs" / "N160",
                ROOT / "cases/lid_driven_cavity",
                latest_time=60,
            )
        )

        for resolution, (exact_u, exact_v) in exact_profiles.items():
            exact_u_rows.extend(profile_table_rows(resolution, "exact_original", exact_u, "y", "Ux"))
            exact_v_rows.extend(profile_table_rows(resolution, "exact_original", exact_v, "x", "Uy"))
            metrics = profile_metrics(exact_u, exact_v)
            error_rows.append(
                {
                    "resolution": resolution,
                    "sampling_method": "exact_sample_original",
                    "actual_line_coordinate": "x=0.5;y=0.5",
                    "RMSE_U": metrics.rmse_u,
                    "Linf_U": metrics.linf_u,
                    "RMSE_V": metrics.rmse_v,
                    "Linf_V": metrics.linf_v,
                }
            )
            pointwise_rows.extend(pointwise_errors(resolution, "exact_sample_original", "u", exact_u, reference_u))
            pointwise_rows.extend(pointwise_errors(resolution, "exact_sample_original", "v", exact_v, reference_v))

        make_self_convergence(exact_profiles, diagnostics_dir)
        freeze_hashes(runs_root, diagnostics_dir)

    write_centerline_profile_table(
        diagnostics_dir / "centerline_u_nearest_cell.csv",
        nearest_u_rows,
        "y",
        "Ux",
    )
    write_centerline_profile_table(
        diagnostics_dir / "centerline_v_nearest_cell.csv",
        nearest_v_rows,
        "x",
        "Uy",
    )
    if exact_u_rows:
        write_centerline_profile_table(
            diagnostics_dir / "centerline_u_exact_sample.csv",
            exact_u_rows,
            "y",
            "Ux",
        )
        write_centerline_profile_table(
            diagnostics_dir / "centerline_v_exact_sample.csv",
            exact_v_rows,
            "x",
            "Uy",
        )

    write_csv(
        diagnostics_dir / "error_by_sampling_method.csv",
        error_rows,
        ["resolution", "sampling_method", "actual_line_coordinate", "RMSE_U", "Linf_U", "RMSE_V", "Linf_V"],
    )
    write_csv(
        diagnostics_dir / "pointwise_reference_errors.csv",
        pointwise_rows,
        ["resolution", "sampling_method", "profile", "coordinate", "cfd_value", "reference_value", "error"],
    )
    write_csv(
        diagnostics_dir / "steady_state_audit.csv",
        steady_rows,
        [
            "resolution",
            "phase",
            "latest_time",
            "previous_time",
            "write_interval_time",
            "relative_L2_change",
            "last_5_write_relative_L2_changes",
            "final_residual",
            "continuity_error",
            "centerline_change_last_20_percent",
            "fixed_5_status",
            "fixed_5_actual_interval",
            "relative_L2_change_over_5_time_units",
        ],
    )
    if extended_rows:
        write_csv(
            diagnostics_dir / "extended_time_comparison.csv",
            extended_rows,
            [
                "resolution",
                "before_final_time",
                "after_final_time",
                "centerline_u_rms_change",
                "centerline_v_rms_change",
                "before_RMSE_U",
                "after_RMSE_U",
                "before_Linf_U",
                "after_Linf_U",
                "before_RMSE_V",
                "after_RMSE_V",
                "before_Linf_V",
                "after_Linf_V",
                "fixed_5_actual_interval",
                "relative_L2_change_over_5_time_units",
            ],
        )
    plot_pointwise_errors(pointwise_rows, diagnostics_dir)
    write_summary(
        diagnostics_dir,
        sampling_rows,
        error_rows,
        courant_rows,
        extended_rows,
        version_rows,
        steady_rows,
    )


def extension_row(
    resolution: int,
    before_u: list[tuple[float, float]],
    before_v: list[tuple[float, float]],
    before_metrics: ProfileMetrics,
    after_u: list[tuple[float, float]],
    after_v: list[tuple[float, float]],
    after_metrics: ProfileMetrics,
    fixed: dict[str, object],
) -> dict[str, object]:
    return {
        "resolution": resolution,
        "before_final_time": 30 if resolution == 80 else 40,
        "after_final_time": 50 if resolution == 80 else 60,
        "centerline_u_rms_change": rms_difference(after_u, before_u),
        "centerline_v_rms_change": rms_difference(after_v, before_v),
        "before_RMSE_U": before_metrics.rmse_u,
        "after_RMSE_U": after_metrics.rmse_u,
        "before_Linf_U": before_metrics.linf_u,
        "after_Linf_U": after_metrics.linf_u,
        "before_RMSE_V": before_metrics.rmse_v,
        "after_RMSE_V": after_metrics.rmse_v,
        "before_Linf_V": before_metrics.linf_v,
        "after_Linf_V": after_metrics.linf_v,
        "fixed_5_actual_interval": fixed.get("actual_interval", ""),
        "relative_L2_change_over_5_time_units": fixed.get("relative_L2_change_over_5_time_units", ""),
    }


def monotonic_decreasing(values: list[float]) -> bool:
    return all(a > b for a, b in zip(values, values[1:]))


def write_summary(
    diagnostics_dir: Path,
    sampling_rows: list[dict[str, object]],
    error_rows: list[dict[str, object]],
    courant_rows: list[dict[str, object]],
    extended_rows: list[dict[str, object]],
    version_rows: list[dict[str, object]],
    steady_rows: list[dict[str, object]],
) -> None:
    nearest_offsets = [abs(float(row["offset_from_0p5"])) for row in sampling_rows]
    exact_rows = [row for row in error_rows if row["sampling_method"] == "exact_sample_original"]
    nearest_rows = [row for row in error_rows if row["sampling_method"] == "nearest_cell_original"]
    exact_error_totals = [
        float(row["RMSE_U"]) + float(row["RMSE_V"])
        for row in sorted(exact_rows, key=lambda item: int(item["resolution"]))
    ]
    nearest_error_totals = [
        float(row["RMSE_U"]) + float(row["RMSE_V"])
        for row in sorted(nearest_rows, key=lambda item: int(item["resolution"]))
    ]
    self_path = diagnostics_dir / "self_convergence.csv"
    self_established = False
    if self_path.exists():
        self_rows = list(csv.DictReader(self_path.open()))
        self_established = bool(self_rows) and all(
            row["observed_order_status"] == "established" for row in self_rows
        )
    original_intervals = [
        float(row["write_interval_time"])
        for row in steady_rows
        if row["phase"] == "original_runs" and row["write_interval_time"] not in {"", None}
    ]
    fixed_intervals = [
        float(row["fixed_5_actual_interval"])
        for row in steady_rows
        if row["phase"] == "diagnostic_original_rerun"
        and row["fixed_5_status"] == "available"
        and row["fixed_5_actual_interval"] not in {"", None}
    ]
    steady_comparable = (
        len(original_intervals) == len(RESOLUTIONS)
        and max(original_intervals) - min(original_intervals) < 1e-12
        and len(fixed_intervals) == len(RESOLUTIONS)
        and max(fixed_intervals) - min(fixed_intervals) < 1e-12
    )
    payload = {
        "nearest_cell_sampling_bias_detected": any(offset > 0 for offset in nearest_offsets),
        "exact_sampling_restores_monotonicity": bool(exact_error_totals)
        and monotonic_decreasing(exact_error_totals),
        "steady_state_criterion_comparable_across_grids": steady_comparable,
        "n80_changed_after_extension": any(
            int(row["resolution"]) == 80
            and (
                float(row["centerline_u_rms_change"]) > 1e-8
                or float(row["centerline_v_rms_change"]) > 1e-8
            )
            for row in extended_rows
        ),
        "n160_changed_after_extension": any(
            int(row["resolution"]) == 160
            and (
                float(row["centerline_u_rms_change"]) > 1e-8
                or float(row["centerline_v_rms_change"]) > 1e-8
            )
            for row in extended_rows
        ),
        "maxCo_passed_all_grids": all(not row["max_Co_exceeded_0p5"] for row in courant_rows),
        "self_convergence_established": self_established,
        "reference_error_monotonic": bool(nearest_error_totals)
        and monotonic_decreasing(nearest_error_totals),
        "grid_run_openfoam_version_status": "OpenFOAM-10"
        if all(
            row["context"] != "original_grid_run" or row["openfoam_version"] == "10"
            for row in version_rows
        )
        else "check",
    }
    write_json(diagnostics_dir / "diagnostic_summary.json", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs/cavity_validation")
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        default=ROOT / "runs/cavity_validation/diagnostics",
    )
    parser.add_argument("--run-openfoam", action="store_true")
    args = parser.parse_args()
    run_diagnostics(args.runs_root, args.diagnostics_dir, run_openfoam=args.run_openfoam)
    print(f"Wrote diagnostics under {args.diagnostics_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
