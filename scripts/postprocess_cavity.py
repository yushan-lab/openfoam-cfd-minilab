#!/usr/bin/env python3
"""Create centerline CSVs and figures from real OpenFOAM solver outputs."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
VECTOR_RE = re.compile(
    rf"\(\s*({NUMBER_PATTERN})\s+({NUMBER_PATTERN})\s+({NUMBER_PATTERN})\s*\)"
)


@dataclass(frozen=True)
class VectorSample:
    x: float
    y: float
    z: float
    Ux: float
    Uy: float
    Uz: float


def strip_openfoam_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


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


def read_internal_vector_field(path: Path, expected_count: int | None = None) -> list[tuple[float, float, float]]:
    text = strip_openfoam_comments(path.read_text())

    uniform_match = re.search(
        rf"internalField\s+uniform\s+{VECTOR_RE.pattern}\s*;",
        text,
        flags=re.DOTALL,
    )
    if uniform_match:
        vector = tuple(float(value) for value in uniform_match.groups()[-3:])
        if expected_count is None:
            return [vector]
        return [vector] * expected_count

    nonuniform_match = re.search(
        r"internalField\s+nonuniform\s+\S+\s+(\d+)\s*\(\s*(.*?)\s*\)\s*;",
        text,
        flags=re.DOTALL,
    )
    if not nonuniform_match:
        raise ValueError(f"Could not parse internalField vector data from {path}")

    declared_count = int(nonuniform_match.group(1))
    body = nonuniform_match.group(2)
    vectors = [
        (float(match.group(1)), float(match.group(2)), float(match.group(3)))
        for match in VECTOR_RE.finditer(body)
    ]
    if len(vectors) != declared_count:
        raise ValueError(
            f"{path} declares {declared_count} vectors but {len(vectors)} were parsed."
        )
    if expected_count is not None and len(vectors) != expected_count:
        raise ValueError(
            f"{path} has {len(vectors)} vectors, expected {expected_count} from blockMeshDict."
        )
    return vectors


def parse_block_mesh_vertices(block_mesh_path: Path) -> list[tuple[float, float, float]]:
    text = strip_openfoam_comments(block_mesh_path.read_text())
    vertices_match = re.search(r"vertices\s*\(\s*(.*?)\s*\)\s*;", text, flags=re.DOTALL)
    if not vertices_match:
        raise ValueError(f"Could not find vertices block in {block_mesh_path}")

    vertices = [
        (float(match.group(1)), float(match.group(2)), float(match.group(3)))
        for match in VECTOR_RE.finditer(vertices_match.group(1))
    ]
    if not vertices:
        raise ValueError(f"No vertices parsed from {block_mesh_path}")
    return vertices


def parse_block_mesh_resolution(block_mesh_path: Path) -> tuple[int, int, int]:
    text = strip_openfoam_comments(block_mesh_path.read_text())
    block_match = re.search(
        r"hex\s*\([^)]*\)\s*\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\)",
        text,
        flags=re.DOTALL,
    )
    if not block_match:
        raise ValueError(f"Could not find single hex block resolution in {block_mesh_path}")
    return tuple(int(value) for value in block_match.groups())


def reconstruct_structured_cell_centres(block_mesh_path: Path) -> list[tuple[float, float, float]]:
    vertices = parse_block_mesh_vertices(block_mesh_path)
    nx, ny, nz = parse_block_mesh_resolution(block_mesh_path)
    xs = [vertex[0] for vertex in vertices]
    ys = [vertex[1] for vertex in vertices]
    zs = [vertex[2] for vertex in vertices]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)

    centres: list[tuple[float, float, float]] = []
    for k in range(nz):
        z = zmin + (k + 0.5) * (zmax - zmin) / nz
        for j in range(ny):
            y = ymin + (j + 0.5) * (ymax - ymin) / ny
            for i in range(nx):
                x = xmin + (i + 0.5) * (xmax - xmin) / nx
                centres.append((round(x, 12), round(y, 12), round(z, 12)))
    return centres


def find_latest_numeric_time_dir(case_dir: Path) -> Path:
    numeric_dirs: list[tuple[float, Path]] = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            numeric_dirs.append((float(path.name), path))
        except ValueError:
            continue

    if not numeric_dirs:
        raise FileNotFoundError(f"No numeric OpenFOAM time directories found under {case_dir}")
    return max(numeric_dirs, key=lambda item: item[0])[1]


def load_final_velocity_cells(case_dir: Path) -> tuple[list[VectorSample], str, str]:
    latest_time_dir = find_latest_numeric_time_dir(case_dir)
    block_mesh_path = case_dir / "system" / "blockMeshDict"
    reconstructed_centres = reconstruct_structured_cell_centres(block_mesh_path)
    velocities = read_internal_vector_field(latest_time_dir / "U", expected_count=len(reconstructed_centres))

    centre_path = latest_time_dir / "C"
    if centre_path.exists():
        centres = read_internal_vector_field(centre_path, expected_count=len(velocities))
        centre_source = "C"
    else:
        centres = reconstructed_centres
        centre_source = "reconstructed from blockMeshDict"

    cells = [
        VectorSample(x, y, z, ux, uy, uz)
        for (x, y, z), (ux, uy, uz) in zip(centres, velocities)
    ]
    return cells, latest_time_dir.name, centre_source


def extract_nearest_centerline(
    cells: list[VectorSample],
    *,
    fixed_axis: str,
    fixed_value: float,
    varying_axis: str,
) -> list[VectorSample]:
    grouped: dict[float, list[VectorSample]] = {}
    for cell in cells:
        key = round(getattr(cell, varying_axis), 12)
        grouped.setdefault(key, []).append(cell)

    rows: list[VectorSample] = []
    for key in sorted(grouped):
        candidates = grouped[key]
        rows.append(
            min(
                candidates,
                key=lambda cell: (
                    abs(getattr(cell, fixed_axis) - fixed_value),
                    getattr(cell, fixed_axis),
                ),
            )
        )
    return rows


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
    axes[0].set_xlabel("Ux near x = 0.5")
    axes[0].set_ylabel("y")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot([row.x for row in horizontal_rows], [row.Uy for row in horizontal_rows], linewidth=1.8)
    axes[1].set_xlabel("x")
    axes[1].set_ylabel("Uy near y = 0.5")
    axes[1].grid(True, alpha=0.3)

    fig.suptitle("Lid-driven cavity nearest-cell centerline profiles")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_velocity_magnitude_from_cells(cells: list[VectorSample], output_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.tri as tri

    x_values = [cell.x for cell in cells]
    y_values = [cell.y for cell in cells]
    magnitudes = [(cell.Ux**2 + cell.Uy**2 + cell.Uz**2) ** 0.5 for cell in cells]

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

    cells, latest_time, centre_source = load_final_velocity_cells(args.case)
    vertical_rows = extract_nearest_centerline(
        cells, fixed_axis="x", fixed_value=0.5, varying_axis="y"
    )
    horizontal_rows = extract_nearest_centerline(
        cells, fixed_axis="y", fixed_value=0.5, varying_axis="x"
    )

    write_centerline_csv(args.results / "centerline_u.csv", vertical_rows, coordinate="y", component="Ux")
    write_centerline_csv(args.results / "centerline_v.csv", horizontal_rows, coordinate="x", component="Uy")
    plot_centerlines(vertical_rows, horizontal_rows, args.figures / "cavity_centerline_profiles.png")
    plot_velocity_magnitude_from_cells(cells, args.figures / "cavity_velocity_magnitude.png")

    print(f"Read final-time U from {args.case / latest_time / 'U'}")
    print(f"Cell centre source: {centre_source}")
    print(f"Wrote {args.results / 'centerline_u.csv'}")
    print(f"Wrote {args.results / 'centerline_v.csv'}")
    print(f"Wrote {args.figures / 'cavity_centerline_profiles.png'}")
    print(f"Wrote {args.figures / 'cavity_velocity_magnitude.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
