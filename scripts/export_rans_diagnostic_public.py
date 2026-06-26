#!/usr/bin/env python3
"""Export canonical post-continuation RANS diagnostic results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import compare_rans_pitzdaily_models as compare  # noqa: E402
import plot_rans_pitzdaily_fields as field_plots  # noqa: E402
from public_readme import (  # noqa: E402
    ensure_combined_title_and_opening,
    replace_or_insert_marked_section,
    replace_or_insert_section_before,
)
import rans_pitzdaily_formal_tools as tools  # noqa: E402


MODELS = ("kEpsilon", "kOmegaSST")
CANONICAL_ROOT = ROOT / "runs/rans_pitzdaily_formal_v2/stability_continuation"
CANONICAL_PROFILE = "continuation_common"
PRE_ROOT = ROOT / "runs/rans_pitzdaily_formal_v2"
PRE_PROFILE = "conservative_common"
PUBLIC_RESULTS = ROOT / "results/public/rans_pitzdaily"
PUBLIC_FIGURES = ROOT / "figures/rans_pitzdaily"
LEGACY_FIGURE_DIR = PUBLIC_FIGURES / "legacy/pre_continuation"
README_START = "<!-- rans-pitzdaily:start -->"
README_END = "<!-- rans-pitzdaily:end -->"
SST_PREVIOUS_TIME = "1702"
SST_FINAL_TIME = "1802"
WALL_SHEAR_THRESHOLD = 0.03

RANS_REPRODUCTION_SECTION = """## RANS Reproduction

Local RANS execution requires an OpenFOAM-10-enabled shell. The GitHub Actions workflow in this repository is a lightweight cavity smoke workflow; it does not run the full RANS diagnostic study.

Run paired smoke cases:

```bash
MODEL=kEpsilon OVERWRITE=1 bash scripts/run_rans_pitzdaily_case.sh
MODEL=kOmegaSST OVERWRITE=1 bash scripts/run_rans_pitzdaily_case.sh
```

Run the formal paired pitzDaily workflow:

```bash
OVERWRITE=1 bash scripts/run_rans_pitzdaily_formal.sh
```

Run the QoI stability audit on existing formal-run fields:

```bash
python scripts/audit_rans_qoi_stability.py --output-root runs/rans_pitzdaily_formal_v2 --selected-profile conservative_common
```

Run the fixed +300 iteration continuation when the stability audit requires it:

```bash
python scripts/run_rans_qoi_stability_continuation.py --source-root runs/rans_pitzdaily_formal_v2 --output-root runs/rans_pitzdaily_formal_v2/stability_continuation --selected-profile conservative_common --additional-iterations 300 --write-interval 100 --overwrite
```

Export the public RANS diagnostic summaries and figures:

```bash
python scripts/export_rans_diagnostic_public.py
```

Export both public studies and run the public artifact audit:

```bash
python scripts/export_all_public.py
```

The `runs/` directory stores local solver fields and logs and is not tracked by git. Lightweight public RANS CSV/JSON files are written to `results/public/rans_pitzdaily/`; public RANS figures are written to `figures/rans_pitzdaily/`.
"""


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


def as_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def qoi_by_model(root: Path) -> dict[str, dict[str, Any]]:
    return {row["model"]: row for row in read_csv_rows(root / "final_audit/qoi_stability.csv")}


def summary_by_model(root: Path) -> dict[str, dict[str, Any]]:
    return {row["model"]: row for row in read_csv_rows(root / "final_audit/selected_model_summary.csv")}


def snapshot_registry(pre_root: Path = PRE_ROOT, canonical_root: Path = CANONICAL_ROOT) -> dict[str, Any]:
    pre_qoi = qoi_by_model(pre_root)
    canonical_qoi = qoi_by_model(canonical_root)
    pre_summary = summary_by_model(pre_root)
    canonical_summary = summary_by_model(canonical_root)
    snapshots: list[dict[str, Any]] = []
    for snapshot_id, root, summaries, qoi, intended_use in [
        (
            "historical_pre_stability_snapshot",
            pre_root / PRE_PROFILE,
            pre_summary,
            pre_qoi,
            "historical_pre_stability_snapshot",
        ),
        (
            "canonical_diagnostic_snapshot",
            canonical_root / CANONICAL_PROFILE,
            canonical_summary,
            canonical_qoi,
            "canonical_diagnostic_snapshot",
        ),
    ]:
        for model in MODELS:
            row = summaries.get(model, {})
            qoi_row = qoi.get(model, {})
            snapshots.append(
                {
                    "snapshot_id": snapshot_id,
                    "source_root": display_path(root),
                    "model": model,
                    "final_iteration": int(float(row.get("actual_iterations") or qoi_row.get("final_iteration") or 0)),
                    "status": row.get("status"),
                    "quality_gate_status": row.get("quality_gate_status"),
                    "intended_use": intended_use,
                    "qoi_stability_status": "passed"
                    if str(qoi_row.get("qoi_stability_passed")).lower() == "true"
                    else "failed",
                }
            )
    return {"snapshots": snapshots}


def solver_profile() -> dict[str, Any]:
    return {
        "profile": "conservative_common",
        "base_solver_profile": "conservative_common",
        "canonical_snapshot_profile": CANONICAL_PROFILE,
        "U_exact_entry": 0.7,
        "equation_catch_all_regex": ".*",
        "equation_catch_all_value": 0.5,
        "continuation_additional_iterations": 300,
        "continuation_residual_control_disabled": True,
        "applied_to_models": list(MODELS),
        "openfoam_version": "10",
    }


def face_area(points: list[tuple[float, float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    ax = ay = az = 0.0
    for current, nxt in zip(points, points[1:] + points[:1]):
        ax += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ay += (current[2] - nxt[2]) * (current[0] + nxt[0])
        az += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return 0.5 * math.sqrt(ax * ax + ay * ay + az * az)


def patch_face_geometry(case_dir: Path, patch: str) -> list[dict[str, Any]]:
    boundary = tools.parse_poly_boundary(case_dir / "constant/polyMesh/boundary")
    if patch not in boundary:
        return []
    points = tools.read_openfoam_list(case_dir / "constant/polyMesh/points", "vector")
    faces = tools.read_openfoam_list(case_dir / "constant/polyMesh/faces", "face")
    info = boundary[patch]
    rows = []
    for index, face in enumerate(faces[info["startFace"] : info["startFace"] + info["nFaces"]]):
        coords = [points[item] for item in face]
        centre = tuple(sum(coord[i] for coord in coords) / len(coords) for i in range(3))
        area = face_area(coords)
        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        wall_face_length = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2)
        rows.append(
            {
                "face_index": index,
                "x": centre[0],
                "y": centre[1],
                "z": centre[2],
                "face_area": area,
                "wall_face_length": wall_face_length,
            }
        )
    return rows


def wall_shear_rows(case_dir: Path, model: str, time_name: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in tools.collect_wall_shear(case_dir, model, time_name)
        if row.get("patch") == "lowerWall"
    ]
    geometry = {row["face_index"]: row for row in patch_face_geometry(case_dir, "lowerWall")}
    enriched = []
    for row in rows:
        geo = geometry.get(int(row["face_index"]), {})
        enriched.append(
            {
                "time": time_name,
                "face_index": int(row["face_index"]),
                "x": float(row["x"]),
                "y": float(row["y"]),
                "z": float(row["z"]),
                "face_area": float(geo.get("face_area", 0.0)),
                "wall_face_length": float(geo.get("wall_face_length", 0.0)),
                "tau_downstream_tangent": float(row.get("tau_downstream_tangent") or row["tau_streamwise"]),
            }
        )
    return sorted(enriched, key=lambda row: row["face_index"])


def duplicate_coordinate_count(rows: list[dict[str, Any]]) -> int:
    seen: set[tuple[float, float, float]] = set()
    duplicates = 0
    for row in rows:
        key = (row["x"], row["y"], row["z"])
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def coordinate_audit(previous: list[dict[str, Any]], final: list[dict[str, Any]]) -> dict[str, Any]:
    face_count_match = len(previous) == len(final)
    face_index_match = [row["face_index"] for row in previous] == [row["face_index"] for row in final]
    coords_previous = [(row["x"], row["y"], row["z"]) for row in previous]
    coords_final = [(row["x"], row["y"], row["z"]) for row in final]
    coordinate_match = coords_previous == coords_final
    monotonic_previous = all(a <= b for a, b in zip([row["x"] for row in previous], [row["x"] for row in previous][1:]))
    monotonic_final = all(a <= b for a, b in zip([row["x"] for row in final], [row["x"] for row in final][1:]))
    return {
        "face_count_previous": len(previous),
        "face_count_final": len(final),
        "face_count_match": face_count_match,
        "face_index_match": face_index_match,
        "coordinate_match": coordinate_match,
        "duplicate_coordinates_previous": duplicate_coordinate_count(previous),
        "duplicate_coordinates_final": duplicate_coordinate_count(final),
        "ordering_match": coords_previous == coords_final,
        "x_monotonic_previous": monotonic_previous,
        "x_monotonic_final": monotonic_final,
        "direct_index_l2_allowed": face_count_match and face_index_match and coordinate_match,
        "fallback_method": "direct_face_index" if face_count_match and face_index_match and coordinate_match else "common_x_interpolation",
    }


def aligned_wall_shear(previous: list[dict[str, Any]], final: list[dict[str, Any]], audit: dict[str, Any]) -> list[dict[str, Any]]:
    if not audit["direct_index_l2_allowed"]:
        raise ValueError("Coordinate mismatch requires interpolation; current exporter only accepts identical lowerWall coordinates")
    rows = []
    for prev, cur in zip(previous, final):
        rows.append(
            {
                "face_index": prev["face_index"],
                "x": prev["x"],
                "y": prev["y"],
                "z": prev["z"],
                "face_area": prev["face_area"],
                "wall_face_length": prev["wall_face_length"],
                "tau_previous": prev["tau_downstream_tangent"],
                "tau_final": cur["tau_downstream_tangent"],
                "tau_delta": cur["tau_downstream_tangent"] - prev["tau_downstream_tangent"],
                "abs_delta": abs(cur["tau_downstream_tangent"] - prev["tau_downstream_tangent"]),
            }
        )
    return rows


def relative_l2(previous: list[float], final: list[float], weights: list[float] | None = None) -> float:
    if weights is None:
        weights = [1.0] * len(previous)
    numerator = math.sqrt(sum(w * (b - a) ** 2 for a, b, w in zip(previous, final, weights)))
    denominator = math.sqrt(sum(w * b * b for b, w in zip(final, weights)))
    return numerator / max(denominator, 1e-30)


def weighted_integral(values: list[float], weights: list[float]) -> float:
    return sum(value * weight for value, weight in zip(values, weights))


def negative_integral(values: list[float], weights: list[float]) -> float:
    return sum(min(value, 0.0) * weight for value, weight in zip(values, weights))


def relative_change(previous: float, final: float) -> float:
    return abs(final - previous) / max(abs(previous), abs(final), 1e-30)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def region_name(x: float, step_x: float, step_height: float, reattachment_x: float) -> str:
    if x < step_x:
        return "upstream_attached_wall"
    if x < step_x + step_height:
        return "near_step_region"
    if abs(x - reattachment_x) <= step_height:
        return "reattachment_neighbourhood"
    if x < reattachment_x - step_height:
        return "separated_region"
    return "post_reattachment_region"


def region_metrics(rows: list[dict[str, Any]], step_x: float, step_height: float, reattachment_x: float) -> list[dict[str, Any]]:
    regions = [
        "upstream_attached_wall",
        "near_step_region",
        "separated_region",
        "reattachment_neighbourhood",
        "post_reattachment_region",
    ]
    by_region = {name: [] for name in regions}
    for row in rows:
        by_region[region_name(row["x"], step_x, step_height, reattachment_x)].append(row)
    metrics = []
    for name, items in by_region.items():
        previous = [row["tau_previous"] for row in items]
        final = [row["tau_final"] for row in items]
        weights = [row["face_area"] or row["wall_face_length"] or 1.0 for row in items]
        abs_delta = [row["abs_delta"] for row in items]
        metrics.append(
            {
                "metric_scope": name,
                "count": len(items),
                "unweighted_relative_L2": relative_l2(previous, final) if items else None,
                "face_area_weighted_relative_L2": relative_l2(previous, final, weights) if items else None,
                "max_absolute_change": max(abs_delta) if abs_delta else None,
                "median_absolute_change": percentile(abs_delta, 0.5),
                "p95_absolute_change": percentile(abs_delta, 0.95),
                "relative_change_of_curve_integral": relative_change(weighted_integral(previous, weights), weighted_integral(final, weights))
                if items
                else None,
                "relative_change_of_negative_shear_integral": relative_change(negative_integral(previous, weights), negative_integral(final, weights))
                if items
                else None,
            }
        )
    return metrics


def wall_shear_stability(case_dir: Path) -> dict[str, Any]:
    previous = wall_shear_rows(case_dir, "kOmegaSST", SST_PREVIOUS_TIME)
    final = wall_shear_rows(case_dir, "kOmegaSST", SST_FINAL_TIME)
    audit = coordinate_audit(previous, final)
    aligned = aligned_wall_shear(previous, final, audit)
    previous_values = [row["tau_previous"] for row in aligned]
    final_values = [row["tau_final"] for row in aligned]
    weights = [row["face_area"] or row["wall_face_length"] or 1.0 for row in aligned]
    abs_delta = [row["abs_delta"] for row in aligned]
    reattachment = load_json(case_dir / "results/reattachment_summary.json")
    geometry = load_json(case_dir / "results/geometry_audit.json")
    step_x = float(geometry.get("step_x") or 0.0)
    step_height = float(geometry.get("step_height") or 0.0254)
    reattachment_x = float(reattachment.get("x_reattachment_raw"))
    overall = {
        "metric_scope": "overall_lowerWall",
        "count": len(aligned),
        "unweighted_relative_L2": relative_l2(previous_values, final_values),
        "face_area_weighted_relative_L2": relative_l2(previous_values, final_values, weights),
        "max_absolute_change": max(abs_delta),
        "median_absolute_change": percentile(abs_delta, 0.5),
        "p95_absolute_change": percentile(abs_delta, 0.95),
        "relative_change_of_curve_integral": relative_change(weighted_integral(previous_values, weights), weighted_integral(final_values, weights)),
        "relative_change_of_negative_shear_integral": relative_change(negative_integral(previous_values, weights), negative_integral(final_values, weights)),
        "threshold": WALL_SHEAR_THRESHOLD,
        "gate_passed": relative_l2(previous_values, final_values) <= WALL_SHEAR_THRESHOLD,
    }
    return {
        "previous": previous,
        "final": final,
        "coordinate_audit": audit,
        "aligned": aligned,
        "summary_rows": [overall, *region_metrics(aligned, step_x, step_height, reattachment_x)],
        "step_x": step_x,
        "step_height": step_height,
        "reattachment_x": reattachment_x,
    }


def write_wall_shear_diagnostics(canonical_root: Path = CANONICAL_ROOT) -> dict[str, Any]:
    final_audit = canonical_root / "final_audit"
    case_dir = canonical_root / CANONICAL_PROFILE / "kOmegaSST"
    report = wall_shear_stability(case_dir)
    coord_rows = []
    by_final = {row["face_index"]: row for row in report["final"]}
    for previous in report["previous"]:
        final = by_final.get(previous["face_index"], {})
        coord_rows.append(
            {
                "face_index": previous["face_index"],
                "x_previous": previous["x"],
                "y_previous": previous["y"],
                "z_previous": previous["z"],
                "x_final": final.get("x"),
                "y_final": final.get("y"),
                "z_final": final.get("z"),
                "face_area": previous["face_area"],
                "wall_face_length": previous["wall_face_length"],
                "tau_downstream_tangent_previous": previous["tau_downstream_tangent"],
                "tau_downstream_tangent_final": final.get("tau_downstream_tangent"),
                "coordinates_match": (
                    previous.get("x"),
                    previous.get("y"),
                    previous.get("z"),
                )
                == (
                    final.get("x"),
                    final.get("y"),
                    final.get("z"),
                ),
            }
        )
    write_csv(final_audit / "sst_wall_shear_coordinate_audit.csv", coord_rows)
    write_json(final_audit / "sst_wall_shear_coordinate_audit.json", report["coordinate_audit"])
    write_csv(final_audit / "sst_wall_shear_stability.csv", report["summary_rows"])
    return report


def plot_wall_shear_stability(report: dict[str, Any], figure_dir: Path = PUBLIC_FIGURES) -> None:
    figure_dir.mkdir(parents=True, exist_ok=True)
    aligned = report["aligned"]
    xs = [row["x"] for row in aligned]
    previous = [row["tau_previous"] for row in aligned]
    final = [row["tau_final"] for row in aligned]
    deltas = [row["tau_delta"] for row in aligned]
    step_x = report["step_x"]
    step_height = report["step_height"]
    reattachment_x = report["reattachment_x"]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, previous, label=f"kOmegaSST {SST_PREVIOUS_TIME}")
    ax.plot(xs, final, label=f"kOmegaSST {SST_FINAL_TIME}")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.axvline(step_x, color="0.25", linestyle="--", linewidth=0.9, label="step")
    ax.axvline(reattachment_x, color="tab:red", linestyle=":", linewidth=1.2, label="reattachment")
    ax.set_xlabel("x")
    ax.set_ylabel("Kinematic wall shear, downstream tangent")
    ax.set_title("kOmegaSST lower-wall shear stability")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    save_figure_if_changed(fig, figure_dir / "sst_wall_shear_stability.png", dpi=160)
    plt.close(fig)

    max_row = max(aligned, key=lambda row: row["abs_delta"])
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(xs, deltas, label=f"tau_{SST_FINAL_TIME} - tau_{SST_PREVIOUS_TIME}")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.axvspan(step_x + step_height, max(reattachment_x - step_height, step_x + step_height), color="tab:orange", alpha=0.15, label="separated region")
    ax.axvspan(reattachment_x - step_height, reattachment_x + step_height, color="tab:red", alpha=0.12, label="reattachment neighbourhood")
    ax.axvline(max_row["x"], color="tab:purple", linestyle="--", linewidth=1.1, label=f"max |change| x={max_row['x']:.4f}")
    ax.set_xlabel("x")
    ax.set_ylabel("Delta kinematic wall shear")
    ax.set_title("kOmegaSST lower-wall shear pointwise change")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    save_figure_if_changed(fig, figure_dir / "sst_wall_shear_pointwise_delta.png", dpi=160)
    plt.close(fig)


def move_legacy_figures(public_figures: Path = PUBLIC_FIGURES, pre_root: Path = PRE_ROOT) -> list[str]:
    names = [
        "field_velocity_comparison.png",
        "field_pressure_comparison.png",
        "field_model_difference.png",
        "turbulent_viscosity_ratio_comparison.png",
        "lower_wall_shear_comparison.png",
        "yplus_distribution_comparison.png",
        "pressure_recovery_comparison.png",
    ]
    copied = []
    LEGACY_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = pre_root / "comparison/figures" / name
        if src.exists():
            dst = LEGACY_FIGURE_DIR / name
            copy_if_changed(src, dst)
            copied.append(dst.as_posix())
    return copied


def diagnostic_model_summary(canonical_root: Path = CANONICAL_ROOT) -> list[dict[str, Any]]:
    summaries = summary_by_model(canonical_root)
    qoi = qoi_by_model(canonical_root)
    rows = []
    for model in MODELS:
        summary = summaries[model]
        qoi_row = qoi[model]
        rows.append(
            {
                "snapshot_id": "canonical_diagnostic_snapshot",
                "model": model,
                "final_iteration": int(float(summary["actual_iterations"])),
                "status": summary["status"],
                "quality_gate_status": summary["quality_gate_status"],
                "pressure_recovery_kinematic": summary["pressure_recovery_kinematic"],
                "lowerWall_yplus_median": summary["lowerWall_yplus_median"],
                "lowerWall_yplus_p95": summary["lowerWall_yplus_p95"],
                "reattachment_length_normalized": summary["reattachment_length_normalized"],
                "qoi_stability_passed": qoi_row["qoi_stability_passed"],
                "wall_shear_curve_relative_L2": qoi_row["wall_shear_curve_relative_L2"],
                "intended_use": "paired RANS diagnostic",
            }
        )
    return rows


def write_public_manifest(public_results: Path = PUBLIC_RESULTS, canonical_root: Path = CANONICAL_ROOT) -> dict[str, Any]:
    rows = diagnostic_model_summary(canonical_root)
    manifest = {
        "run_id": "rans_pitzdaily_formal_v2",
        "snapshot_id": "canonical_diagnostic_snapshot",
        "selected_profile": CANONICAL_PROFILE,
        "intended_use": "paired RANS model diagnostic",
        "models": rows,
    }
    if not field_plots.public_manifest_has_no_absolute_paths(manifest):
        raise ValueError("public manifest contains an absolute path")
    write_json(public_results / "run_manifest_public.json", manifest)
    return manifest


def update_readme(readme: Path, rows: list[dict[str, Any]], wall_shear_rows: list[dict[str, Any]]) -> None:
    by_model = {row["model"]: row for row in rows}
    eps = by_model["kEpsilon"]
    sst = by_model["kOmegaSST"]
    overall = next(row for row in wall_shear_rows if row["metric_scope"] == "overall_lowerWall")
    section = f"""## RANS pitzDaily Paired Diagnostic

This repository also includes a paired RANS model diagnostic for the OpenFOAM `pitzDaily` backward-facing-step tutorial. The diagnostic compares `kEpsilon` and `kOmegaSST` using the same mesh, boundary conditions, numerical schemes, and shared relaxation configuration.

The canonical diagnostic snapshot is the shared +300 iteration continuation under `runs/rans_pitzdaily_formal_v2/stability_continuation/continuation_common/`. The earlier pre-continuation fields are retained only as `historical_pre_stability_snapshot` records in `results/public/rans_pitzdaily/snapshot_registry.json`.

The pre-continuation `conservative_common` runs are the solver-converged baseline: both models triggered SIMPLE convergence and passed solver-integrity, flow-balance, and post-processing quality checks. The public canonical `continuation_common` rows are different: they are fixed +300-iteration QoI stability snapshots advanced from that baseline with `residualControl` disabled. They should not be read as a second SIMPLE-converged solve; their intended use is post-convergence QoI stability audit.

The solver profile and canonical snapshot profile are separate concepts. `results/public/rans_pitzdaily/solver_profile.json` records the common base solver profile as `conservative_common`, while the canonical public snapshot profile is `continuation_common`.

After the fixed +300-iteration continuation, pressure recovery, reattachment location, and lower-wall y+ scalar diagnostics changed by less than 2% over the final 100 iterations. The diagnostic status is `quality_incomplete_comparison` because the full lower-wall shear curve remains the stability boundary: `kEpsilon` passes all QoI stability gates, while `kOmegaSST` has a lower-wall shear curve relative L2 change of `{float(overall['unweighted_relative_L2']) * 100:.2f}%`, above the preregistered 3% gate.

| Model | Iterations | Pressure recovery | Lower-wall y+ median / p95 | Lr/h | QoI stability |
|---|---:|---:|---:|---:|---|
| kEpsilon | {int(float(eps['final_iteration']))} | {float(eps['pressure_recovery_kinematic']):.3f} | {float(eps['lowerWall_yplus_median']):.2f} / {float(eps['lowerWall_yplus_p95']):.2f} | {float(eps['reattachment_length_normalized']):.2f} | passed |
| kOmegaSST | {int(float(sst['final_iteration']))} | {float(sst['pressure_recovery_kinematic']):.3f} | {float(sst['lowerWall_yplus_median']):.2f} / {float(sst['lowerWall_yplus_p95']):.2f} | {float(sst['reattachment_length_normalized']):.2f} | wall-shear L2 not passed |

![RANS velocity field comparison](figures/rans_pitzdaily/field_velocity_comparison.png)

![RANS normalized residual-control history](figures/rans_pitzdaily/normalized_residual_control.png)

![kOmegaSST lower-wall shear stability](figures/rans_pitzdaily/sst_wall_shear_stability.png)

This is a paired RANS model diagnostic and stability-boundary study, not a model-fidelity ranking.

Public RANS diagnostic files:

- `results/public/rans_pitzdaily/diagnostic_model_summary.csv`
- `results/public/rans_pitzdaily/qoi_stability.csv`
- `results/public/rans_pitzdaily/wall_shear_stability_summary.csv`
- `results/public/rans_pitzdaily/quality_gates.csv`
- `results/public/rans_pitzdaily/reattachment_summary.csv`
- `results/public/rans_pitzdaily/run_manifest_public.json`
- `results/public/rans_pitzdaily/snapshot_registry.json`
- `results/public/rans_pitzdaily/solver_profile.json`
- `figures/rans_pitzdaily/field_velocity_comparison.png`
- `figures/rans_pitzdaily/field_model_difference.png`
- `figures/rans_pitzdaily/sst_wall_shear_stability.png`
- `figures/rans_pitzdaily/sst_wall_shear_pointwise_delta.png`
"""
    text = ensure_combined_title_and_opening(readme.read_text(encoding="utf-8"))
    text = replace_or_insert_marked_section(
        text,
        README_START,
        README_END,
        section,
        before_heading="Smoke Reproduction Outputs",
    )
    text = replace_or_insert_section_before(
        text,
        "RANS Reproduction",
        RANS_REPRODUCTION_SECTION,
        before_heading="Cloud Reproduction with GitHub Actions",
    )
    readme.write_text(text, encoding="utf-8")


def export_rans_diagnostic(
    pre_root: Path = PRE_ROOT,
    canonical_root: Path = CANONICAL_ROOT,
    public_results: Path = PUBLIC_RESULTS,
    public_figures: Path = PUBLIC_FIGURES,
    readme: Path = ROOT / "README.md",
) -> dict[str, Any]:
    registry = snapshot_registry(pre_root, canonical_root)
    write_json(pre_root / "final_audit/snapshot_registry.json", registry)
    write_json(public_results / "snapshot_registry.json", registry)
    write_json(public_results / "solver_profile.json", solver_profile())
    wall_report = write_wall_shear_diagnostics(canonical_root)
    plot_wall_shear_stability(wall_report, public_figures)
    moved = move_legacy_figures(public_figures, pre_root)
    compare.generate_comparison(
        canonical_root,
        canonical_root / CANONICAL_PROFILE / "kEpsilon",
        canonical_root / CANONICAL_PROFILE / "kOmegaSST",
    )
    generated = field_plots.generate_outputs(canonical_root, CANONICAL_PROFILE, public_results, public_figures)
    rows = diagnostic_model_summary(canonical_root)
    write_csv(public_results / "diagnostic_model_summary.csv", rows)
    write_csv(public_results / "model_summary.csv", rows)
    write_csv(public_results / "wall_shear_stability_summary.csv", wall_report["summary_rows"])
    stale_relaxation = public_results / "relaxation_profile.json"
    if stale_relaxation.exists():
        stale_relaxation.unlink()
    write_public_manifest(public_results, canonical_root)
    update_readme(readme, rows, wall_report["summary_rows"])
    return {
        "snapshot_registry": registry,
        "wall_shear_coordinate_audit": wall_report["coordinate_audit"],
        "legacy_figures_moved": moved,
        "generated": generated,
        "public_results": public_results.as_posix(),
        "public_figures": public_figures.as_posix(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-root", type=Path, default=PRE_ROOT)
    parser.add_argument("--canonical-root", type=Path, default=CANONICAL_ROOT)
    parser.add_argument("--public-results", type=Path, default=PUBLIC_RESULTS)
    parser.add_argument("--public-figures", type=Path, default=PUBLIC_FIGURES)
    parser.add_argument("--readme", type=Path, default=ROOT / "README.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = export_rans_diagnostic(args.pre_root, args.canonical_root, args.public_results, args.public_figures, args.readme)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
