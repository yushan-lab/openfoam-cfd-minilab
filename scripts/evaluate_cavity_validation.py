#!/usr/bin/env python3
"""Evaluate cavity grid-study outputs against reference or self-convergence data."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ProfileComparison:
    rmse: float
    l_inf: float
    count: int


REFERENCE_SOURCE_FIELDS = ("title", "authors", "year", "source_url", "accessed", "checksum")
PREDICTION_FIELDS = [
    "resolution",
    "profile",
    "coordinate",
    "cfd_value",
    "reference_value",
    "error",
]


def interpolate_profile(points: list[tuple[float, float]], x: float) -> float:
    points = sorted(points)
    if not points:
        raise ValueError("Cannot interpolate an empty profile.")
    if x < points[0][0] or x > points[-1][0]:
        raise ValueError(f"Reference coordinate {x} is outside CFD profile range.")
    for left, right in zip(points, points[1:]):
        x0, y0 = left
        x1, y1 = right
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            weight = (x - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return points[-1][1]


def compare_profile(
    cfd_profile: list[tuple[float, float]],
    reference_profile: list[tuple[float, float]],
) -> ProfileComparison:
    errors = []
    for coordinate, reference_value in reference_profile:
        try:
            cfd_value = interpolate_profile(cfd_profile, coordinate)
        except ValueError:
            continue
        errors.append(cfd_value - reference_value)
    if not errors:
        raise ValueError("No reference profile coordinates fall within the CFD profile range.")
    rmse = (sum(error**2 for error in errors) / len(errors)) ** 0.5
    l_inf = max(abs(error) for error in errors)
    return ProfileComparison(rmse=rmse, l_inf=l_inf, count=len(errors))


def read_two_column_csv(path: Path) -> list[tuple[float, float]]:
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        return [(float(row[0]), float(row[1])) for row in reader if row]


def load_reference_source(path: Path) -> dict[str, object]:
    source = json.loads(path.read_text())
    missing = [field for field in REFERENCE_SOURCE_FIELDS if field not in source or not source[field]]
    if missing:
        missing_fields = ", ".join(missing)
        raise ValueError(f"source.json is missing required metadata: {missing_fields}")
    return source


def load_reference_profiles(reference_dir: Path) -> dict[str, list[tuple[float, float]]]:
    u_path = reference_dir / "re100_centerline_u.csv"
    v_path = reference_dir / "re100_centerline_v.csv"
    source_path = reference_dir / "source.json"
    missing = [path for path in [u_path, v_path, source_path] if not path.exists()]
    if missing:
        missing_names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Reference centerline files are missing: {missing_names}")
    load_reference_source(source_path)
    return {"u": read_two_column_csv(u_path), "v": read_two_column_csv(v_path)}


def load_solver_diagnostics(run_dir: Path) -> dict[str, float | None]:
    summary_path = run_dir / "results" / "solver_summary.json"
    if not summary_path.exists():
        return {"final_residual": None, "continuity_error": None}

    summary = json.loads(summary_path.read_text())
    final_residuals = []
    for residual_data in summary.get("final_residuals", {}).values():
        if "final_residual" in residual_data:
            final_residuals.append(float(residual_data["final_residual"]))

    continuity_data = summary.get("final_continuity_error") or {}
    continuity_error = continuity_data.get("cumulative")
    return {
        "final_residual": max(final_residuals) if final_residuals else None,
        "continuity_error": float(continuity_error) if continuity_error is not None else None,
    }


def load_run_record(run_dir: Path) -> dict[str, object]:
    metadata_path = run_dir / "metadata" / "run_metadata.json"
    if metadata_path.exists():
        record = json.loads(metadata_path.read_text())
    else:
        resolution = int(run_dir.name.lstrip("N"))
        record = {"resolution": resolution, "status": "missing_metadata"}
    record.setdefault("run_dir", str(run_dir))
    record.setdefault("status", "completed")
    for key, value in load_solver_diagnostics(run_dir).items():
        if value is not None:
            record[key] = value
    return record


def load_manifest_records(runs_root: Path) -> list[dict[str, object]]:
    manifest_path = runs_root / "manifest.csv"
    if not manifest_path.exists():
        return []

    records: list[dict[str, object]] = []
    with manifest_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            output_root = Path(row["output_root"])
            local_run_dir = runs_root / f"N{int(row['resolution'])}"
            if not output_root.exists() and local_run_dir.exists():
                output_root = local_run_dir
            if output_root.exists():
                record = load_run_record(output_root)
                record.setdefault("resolution", int(row["resolution"]))
                record["run_dir"] = str(output_root)
                if record.get("status") in (None, "", "missing_metadata"):
                    record["status"] = row["status"]
            else:
                record = {
                    "resolution": int(row["resolution"]),
                    "status": row["status"],
                    "run_dir": row["output_root"],
                }
            records.append(record)
    return records


def load_run_records(runs_root: Path) -> list[dict[str, object]]:
    manifest_records = load_manifest_records(runs_root)
    if manifest_records:
        seen = {Path(str(record["run_dir"])).name for record in manifest_records}
        extra_dirs = sorted(
            [
                path
                for path in runs_root.glob("N*")
                if path.is_dir() and path.name not in seen
            ],
            key=lambda path: int(path.name.lstrip("N")),
        )
        return manifest_records + [load_run_record(run_dir) for run_dir in extra_dirs]

    run_dirs = sorted(
        [path for path in runs_root.glob("N*") if path.is_dir()],
        key=lambda path: int(path.name.lstrip("N")),
    )
    return [load_run_record(run_dir) for run_dir in run_dirs]


SUMMARY_FIELDS = [
    "resolution",
    "status",
    "cell_count",
    "dx",
    "final_time",
    "relative_L2_change",
    "final_residual",
    "continuity_error",
    "wall_clock_seconds",
    "RMSE_U",
    "L_inf_U",
    "RMSE_V",
    "L_inf_V",
    "reference_status",
]


def write_grid_summary(path: Path, records: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in SUMMARY_FIELDS})


def build_prediction_rows(
    run_dir: Path,
    resolution: int,
    reference_profiles: dict[str, list[tuple[float, float]]] | None,
) -> tuple[list[dict[str, object]], dict[str, ProfileComparison]]:
    profiles = {
        "u": read_two_column_csv(run_dir / "results" / "centerline_u.csv"),
        "v": read_two_column_csv(run_dir / "results" / "centerline_v.csv"),
    }
    return build_prediction_rows_from_profiles(
        resolution=resolution,
        profiles=profiles,
        reference_profiles=reference_profiles,
    )


def build_prediction_rows_from_profiles(
    *,
    resolution: int,
    profiles: dict[str, list[tuple[float, float]]],
    reference_profiles: dict[str, list[tuple[float, float]]] | None,
) -> tuple[list[dict[str, object]], dict[str, ProfileComparison]]:
    rows: list[dict[str, object]] = []
    comparisons: dict[str, ProfileComparison] = {}

    for profile_name, cfd_profile in profiles.items():
        if reference_profiles is not None:
            reference_profile = reference_profiles[profile_name]
            comparisons[profile_name] = compare_profile(cfd_profile, reference_profile)
            for coordinate, reference_value in reference_profile:
                try:
                    cfd_value = interpolate_profile(cfd_profile, coordinate)
                except ValueError:
                    continue
                rows.append(
                    {
                        "resolution": resolution,
                        "profile": profile_name,
                        "coordinate": coordinate,
                        "cfd_value": cfd_value,
                        "reference_value": reference_value,
                        "error": cfd_value - reference_value,
                    }
                )
        else:
            for coordinate, cfd_value in cfd_profile:
                rows.append(
                    {
                        "resolution": resolution,
                        "profile": profile_name,
                        "coordinate": coordinate,
                        "cfd_value": cfd_value,
                        "reference_value": "",
                        "error": "",
                    }
                )

    return rows, comparisons


def write_prediction_table(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in PREDICTION_FIELDS})


def _numeric_records(records: Iterable[dict[str, object]], *fields: str) -> list[dict[str, float]]:
    numeric_rows: list[dict[str, float]] = []
    for record in records:
        row: dict[str, float] = {}
        for field in fields:
            value = record.get(field)
            if value in (None, ""):
                break
            row[field] = float(value)
        else:
            numeric_rows.append(row)
    return numeric_rows


def write_grid_figures(
    records: list[dict[str, object]],
    prediction_rows: list[dict[str, object]],
    figures_dir: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_dir.mkdir(parents=True, exist_ok=True)

    reference_rows = [row for row in prediction_rows if row.get("reference_value") != ""]
    if reference_rows:
        fig, axes = plt.subplots(1, 2, figsize=(9, 4))
        for profile_name, axis, x_label, y_label in [
            ("u", axes[0], "Ux", "y"),
            ("v", axes[1], "x", "Uy"),
        ]:
            profile_rows = [row for row in reference_rows if row["profile"] == profile_name]
            resolutions = sorted({int(row["resolution"]) for row in profile_rows})
            for resolution in resolutions:
                rows = [row for row in profile_rows if int(row["resolution"]) == resolution]
                coordinates = [float(row["coordinate"]) for row in rows]
                cfd_values = [float(row["cfd_value"]) for row in rows]
                if profile_name == "u":
                    axis.plot(cfd_values, coordinates, label=f"N{resolution}")
                else:
                    axis.plot(coordinates, cfd_values, label=f"N{resolution}")
            reference_coordinates = [float(row["coordinate"]) for row in profile_rows]
            reference_values = [float(row["reference_value"]) for row in profile_rows]
            if profile_name == "u":
                axis.scatter(reference_values, reference_coordinates, marker="x", label="reference")
            else:
                axis.scatter(reference_coordinates, reference_values, marker="x", label="reference")
            axis.set_xlabel(x_label)
            axis.set_ylabel(y_label)
            axis.grid(True, alpha=0.3)
            axis.legend()
        fig.tight_layout()
        fig.savefig(figures_dir / "cavity_centerline_validation.png", dpi=160)
        plt.close(fig)

    error_rows = _numeric_records(records, "resolution", "RMSE_U", "RMSE_V")
    if error_rows:
        fig, ax = plt.subplots(figsize=(6, 4))
        resolutions = [row["resolution"] for row in error_rows]
        ax.loglog(resolutions, [row["RMSE_U"] for row in error_rows], marker="o", label="RMSE_U")
        ax.loglog(resolutions, [row["RMSE_V"] for row in error_rows], marker="o", label="RMSE_V")
        ax.set_xlabel("Mesh resolution N")
        ax.set_ylabel("RMSE")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures_dir / "cavity_grid_error.png", dpi=160)
        plt.close(fig)

    cost_rows = _numeric_records(records, "wall_clock_seconds", "RMSE_U", "RMSE_V")
    if cost_rows:
        fig, ax = plt.subplots(figsize=(6, 4))
        costs = [row["wall_clock_seconds"] for row in cost_rows]
        ax.loglog(costs, [row["RMSE_U"] for row in cost_rows], marker="o", label="RMSE_U")
        ax.loglog(costs, [row["RMSE_V"] for row in cost_rows], marker="o", label="RMSE_V")
        ax.set_xlabel("Wall-clock time [s]")
        ax.set_ylabel("RMSE")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures_dir / "cavity_error_vs_cost.png", dpi=160)
        plt.close(fig)

    residual_rows = _numeric_records(records, "resolution", "final_residual")
    if residual_rows:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.semilogy(
            [row["resolution"] for row in residual_rows],
            [row["final_residual"] for row in residual_rows],
            marker="o",
        )
        ax.set_xlabel("Mesh resolution N")
        ax.set_ylabel("Max final residual")
        ax.grid(True, which="both", alpha=0.3)
        fig.tight_layout()
        fig.savefig(figures_dir / "cavity_residuals_by_grid.png", dpi=160)
        plt.close(fig)


def write_grid_figures_from_csv(
    summary_csv: Path,
    predictions_csv: Path,
    figures_dir: Path,
) -> None:
    with summary_csv.open(newline="") as handle:
        records = list(csv.DictReader(handle))
    with predictions_csv.open(newline="") as handle:
        prediction_rows = list(csv.DictReader(handle))
    write_grid_figures(records, prediction_rows, figures_dir)


def evaluate_runs(
    runs_root: Path,
    reference_dir: Path,
    output_dir: Path,
    *,
    allow_missing_reference: bool = False,
    figures_dir: Path = Path("figures"),
) -> list[dict[str, object]]:
    records = load_run_records(runs_root)

    try:
        reference_profiles = load_reference_profiles(reference_dir)
        reference_status = "available"
    except FileNotFoundError:
        if not allow_missing_reference:
            raise
        reference_profiles = None
        reference_status = "missing"

    prediction_rows: list[dict[str, object]] = []
    for record in records:
        record["reference_status"] = reference_status
        if record.get("status") != "completed":
            continue
        resolution = int(record["resolution"])
        run_dir = Path(str(record["run_dir"]))
        try:
            rows, comparisons = build_prediction_rows(run_dir, resolution, reference_profiles)
        except FileNotFoundError:
            record["status"] = "missing_centerline_outputs"
            continue
        prediction_rows.extend(rows)
        if reference_profiles is not None:
            record["RMSE_U"] = comparisons["u"].rmse
            record["L_inf_U"] = comparisons["u"].l_inf
            record["RMSE_V"] = comparisons["v"].rmse
            record["L_inf_V"] = comparisons["v"].l_inf

    summary_csv = output_dir / "cavity_grid_summary.csv"
    predictions_csv = output_dir / "cavity_centerline_predictions.csv"
    write_grid_summary(summary_csv, records)
    write_prediction_table(predictions_csv, prediction_rows)
    write_grid_figures_from_csv(summary_csv, predictions_csv, figures_dir)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=Path("runs/cavity_validation"))
    parser.add_argument("--reference-dir", type=Path, default=Path("data/reference"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/public"))
    parser.add_argument("--figures-dir", type=Path, default=Path("figures"))
    parser.add_argument("--allow-missing-reference", action="store_true")
    args = parser.parse_args()

    evaluate_runs(
        args.runs_root,
        args.reference_dir,
        args.output_dir,
        allow_missing_reference=args.allow_missing_reference,
        figures_dir=args.figures_dir,
    )
    print(f"Wrote {args.output_dir / 'cavity_grid_summary.csv'}")
    print(f"Wrote {args.output_dir / 'cavity_centerline_predictions.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
