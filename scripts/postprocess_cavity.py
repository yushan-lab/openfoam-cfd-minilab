#!/usr/bin/env python3
"""Create centerline CSVs and optional figures from real OpenFOAM outputs."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class VectorSample:
    x: float
    y: float
    z: float
    Ux: float
    Uy: float
    Uz: float


def read_vector_sample(path: Path) -> list[VectorSample]:
    rows: list[VectorSample] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [float(item) for item in line.split()]
        if len(parts) < 6:
            raise ValueError(
                f"{path} must contain x y z Ux Uy Uz columns; found {len(parts)} columns."
            )
        rows.append(VectorSample(*parts[:6]))
    return rows


def write_centerline_csv(
    output_path: Path,
    rows: Iterable[VectorSample],
    *,
    coordinate: str,
    component: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([coordinate, component])
        for row in rows:
            writer.writerow([getattr(row, coordinate), getattr(row, component)])


def find_latest_sample(case_dir: Path, name: str) -> Path | None:
    candidates = sorted(
        case_dir.glob(f"postProcessing/**/*{name}*U*.xy"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def plot_centerlines(
    vertical_rows: list[VectorSample],
    horizontal_rows: list[VectorSample],
    output_path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(8, 4), sharey=False)

    axes[0].plot([row.Ux for row in vertical_rows], [row.y for row in vertical_rows], linewidth=1.8)
    axes[0].set_xlabel("Ux at x = 0.5")
    axes[0].set_ylabel("y")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot([row.x for row in horizontal_rows], [row.Uy for row in horizontal_rows], linewidth=1.8)
    axes[1].set_xlabel("x")
    axes[1].set_ylabel("Uy at y = 0.5")
    axes[1].grid(True, alpha=0.3)

    fig.suptitle("Lid-driven cavity centerline velocity profiles")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def find_latest_vtk(case_dir: Path) -> Path | None:
    candidates = sorted(
        case_dir.glob("VTK/**/*.vtk"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def read_legacy_vtk_velocity(path: Path) -> tuple[list[tuple[float, float]], list[float]]:
    point_xyz: list[tuple[float, float, float]] = []
    cells: list[list[int]] = []
    data_target: str | None = None
    data_count: int | None = None
    vector_points: list[tuple[float, float]] = []
    magnitudes: list[float] = []
    lines = path.read_text(errors="ignore").splitlines()

    i = 0
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) >= 3 and parts[0] == "POINTS":
            count = int(parts[1])
            values: list[float] = []
            i += 1
            while len(values) < 3 * count and i < len(lines):
                values.extend(float(item) for item in lines[i].split())
                i += 1
            point_xyz = [
                (values[j], values[j + 1], values[j + 2])
                for j in range(0, 3 * count, 3)
            ]
            continue

        if len(parts) >= 3 and parts[0] == "CELLS":
            count = int(parts[1])
            raw_values: list[int] = []
            i += 1
            while len(cells) < count and i < len(lines):
                raw_values.extend(int(item) for item in lines[i].split())
                cursor = 0
                while cursor < len(raw_values) and len(cells) < count:
                    cell_size = raw_values[cursor]
                    end = cursor + 1 + cell_size
                    if end > len(raw_values):
                        break
                    cells.append(raw_values[cursor + 1 : end])
                    cursor = end
                raw_values = raw_values[cursor:]
                i += 1
            continue

        if len(parts) >= 2 and parts[0] in {"POINT_DATA", "CELL_DATA"}:
            data_target = "point" if parts[0] == "POINT_DATA" else "cell"
            data_count = int(parts[1])
            i += 1
            continue

        if len(parts) >= 3 and parts[0] == "VECTORS" and parts[1] == "U":
            count = data_count if data_count is not None else len(point_xyz)
            values = []
            i += 1
            while len(values) < 3 * count and i < len(lines):
                values.extend(float(item) for item in lines[i].split())
                i += 1
            if len(values) < 3 * count:
                raise ValueError(f"Incomplete VECTORS U data in {path}")

            magnitudes = [
                (values[j] ** 2 + values[j + 1] ** 2 + values[j + 2] ** 2) ** 0.5
                for j in range(0, 3 * count, 3)
            ]

            if data_target == "cell":
                if len(cells) != count:
                    raise ValueError(f"CELL_DATA count does not match CELLS count in {path}")
                vector_points = []
                for cell in cells:
                    xs = [point_xyz[index][0] for index in cell]
                    ys = [point_xyz[index][1] for index in cell]
                    vector_points.append((sum(xs) / len(xs), sum(ys) / len(ys)))
            else:
                if len(point_xyz) < count:
                    raise ValueError(f"POINT_DATA count exceeds POINTS count in {path}")
                vector_points = [(point[0], point[1]) for point in point_xyz[:count]]
            continue
        i += 1

    if not vector_points or not magnitudes:
        raise ValueError(f"Could not find POINTS and VECTORS U data in {path}")
    return vector_points, magnitudes


def plot_velocity_magnitude(vtk_path: Path, output_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.tri as tri

    points, magnitudes = read_legacy_vtk_velocity(vtk_path)
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    triangulation = tri.Triangulation(x_values, y_values)
    fig, ax = plt.subplots(figsize=(5, 4.5))
    contour = ax.tricontourf(triangulation, magnitudes, levels=24, cmap="viridis")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Velocity magnitude")
    fig.colorbar(contour, ax=ax, label="|U|")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cases/lid_driven_cavity"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--figures", type=Path, default=Path("figures"))
    args = parser.parse_args()

    vertical_sample = find_latest_sample(args.case, "verticalCenterline")
    horizontal_sample = find_latest_sample(args.case, "horizontalCenterline")

    if vertical_sample is None or horizontal_sample is None:
        missing = []
        if vertical_sample is None:
            missing.append("verticalCenterline")
        if horizontal_sample is None:
            missing.append("horizontalCenterline")
        raise SystemExit(
            "Missing OpenFOAM sample output for "
            + ", ".join(missing)
            + ". Run postProcess -func sample -latestTime first."
        )

    vertical_rows = read_vector_sample(vertical_sample)
    horizontal_rows = read_vector_sample(horizontal_sample)

    write_centerline_csv(args.results / "centerline_u.csv", vertical_rows, coordinate="y", component="Ux")
    write_centerline_csv(args.results / "centerline_v.csv", horizontal_rows, coordinate="x", component="Uy")
    plot_centerlines(vertical_rows, horizontal_rows, args.figures / "cavity_centerline_profiles.png")

    vtk_path = find_latest_vtk(args.case)
    if vtk_path is not None:
        plot_velocity_magnitude(vtk_path, args.figures / "cavity_velocity_magnitude.png")
        print(f"Wrote {args.figures / 'cavity_velocity_magnitude.png'}")
    else:
        print("No VTK file found; skipped optional velocity magnitude figure.")

    print(f"Wrote {args.results / 'centerline_u.csv'}")
    print(f"Wrote {args.results / 'centerline_v.csv'}")
    print(f"Wrote {args.figures / 'cavity_centerline_profiles.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
