#!/usr/bin/env python3
"""Generate structured blockMeshDict files for the lid-driven cavity case."""

from __future__ import annotations

import argparse
from pathlib import Path


ALLOWED_RESOLUTIONS = {20, 40, 80, 160}


def validate_resolution(resolution: int, *, ny: int | None = None, nz: int = 1) -> int:
    ny = resolution if ny is None else ny
    if resolution != ny:
        raise ValueError("Cavity validation meshes require nx=ny.")
    if nz != 1:
        raise ValueError("Cavity validation meshes require nz=1.")
    if resolution not in ALLOWED_RESOLUTIONS:
        allowed = ", ".join(str(value) for value in sorted(ALLOWED_RESOLUTIONS))
        raise ValueError(f"Unsupported resolution {resolution}. Allowed resolutions: {allowed}.")
    return resolution


def cell_count_for_resolution(resolution: int) -> int:
    resolution = validate_resolution(resolution)
    return resolution * resolution


def render_block_mesh_dict(resolution: int) -> str:
    resolution = validate_resolution(resolution)
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
| OpenFOAM: 2D unit-square lid-driven cavity mesh, {resolution} x {resolution}                  |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}

convertToMeters 1;

vertices
(
    (0 0 -0.005)
    (1 0 -0.005)
    (1 1 -0.005)
    (0 1 -0.005)
    (0 0  0.005)
    (1 0  0.005)
    (1 1  0.005)
    (0 1  0.005)
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({resolution} {resolution} 1) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
    bottom
    {{
        type wall;
        faces
        (
            (0 1 5 4)
        );
    }}

    right
    {{
        type wall;
        faces
        (
            (1 2 6 5)
        );
    }}

    top
    {{
        type wall;
        faces
        (
            (2 3 7 6)
        );
    }}

    left
    {{
        type wall;
        faces
        (
            (3 0 4 7)
        );
    }}

    frontAndBack
    {{
        type empty;
        faces
        (
            (0 3 2 1)
            (4 5 6 7)
        );
    }}
);

mergePatchPairs
(
);
"""


def write_block_mesh_dict(path: Path, resolution: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_block_mesh_dict(resolution), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cases/lid_driven_cavity/system/blockMeshDict"),
    )
    args = parser.parse_args()

    write_block_mesh_dict(args.output, args.resolution)
    print(f"Wrote {args.output} for {args.resolution}x{args.resolution}x1 mesh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
