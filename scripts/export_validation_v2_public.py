#!/usr/bin/env python3
"""Export cavity validation-v2 summaries for public documentation."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs/cavity_validation_v2"
PUBLIC_RESULTS = ROOT / "results/public/cavity_validation_v2"
PUBLIC_FIGURES = ROOT / "figures/cavity_validation_v2"
README = ROOT / "README.md"

README_START = "<!-- cavity-validation-v2:start -->"
README_END = "<!-- cavity-validation-v2:end -->"

CSV_EXPORTS = [
    "validation_summary.csv",
    "self_convergence.csv",
    "quality_gates.csv",
    "manifest.csv",
]

FIGURE_EXPORTS = [
    "exact_centerline_vs_ghia.png",
    "reference_rmse_by_grid.png",
    "grid_to_grid_difference.png",
    "observed_order.png",
    "error_vs_cost.png",
]

README_INTRO = """# OpenFOAM CFD Validation Lab: Re=100 Lid-Driven Cavity Verification

This repository is a controlled numerical validation study for the classical incompressible lid-driven cavity problem at Reynolds number 100. It demonstrates a traditional OpenFOAM workflow: mesh generation, mesh checking, transient solving, residual monitoring, exact centerline sampling, final-time field post-processing, lightweight public export, and smoke reproduction through GitHub Actions.

The full validation-v2 result was generated locally with OpenFOAM-10 across four structured grids. The OpenFOAM-11 GitHub Actions workflow is retained as a smoke reproduction path for the base cavity case, not as the source of the full grid-validation result. This repository is not a production CFD solver or an industrial validation campaign.
"""

MESH_SECTION = """## Mesh

Two mesh contexts are used in this repository:

- Smoke reproduction mesh: `40 x 40 x 1`, used by the base GitHub Actions OpenFOAM-11 workflow.
- Validation-v2 meshes: `20 x 20 x 1`, `40 x 40 x 1`, `80 x 80 x 1`, and `160 x 160 x 1`, used for the OpenFOAM-10 centerline self-convergence study.

The tracked base-case dictionaries are:

- `cases/lid_driven_cavity/system/blockMeshDict.20x20`
- `cases/lid_driven_cavity/system/blockMeshDict.40x40`

`scripts/run_cavity.sh` copies or generates the selected dictionary to `system/blockMeshDict` before running `blockMesh`. Generated OpenFOAM mesh and time directories are reproducible and ignored by git.
"""

PHYSICAL_SETUP_SECTION = """## Physical Setup

- Geometry: unit square cavity, `0 <= x <= 1`, `0 <= y <= 1`.
- Effective 2D treatment: thin `z` direction with `empty` front/back patches.
- Lid velocity: `U = (1, 0, 0)`.
- Characteristic length: `L = 1`.
- Kinematic viscosity: `nu = 0.01`.
- Reynolds number: `Re = U L / nu = 100`.
- Solver: `icoFoam`.
- The case includes `constant/physicalProperties` and `constant/transportProperties` so the same viscosity is explicit across common OpenFOAM variants.
- The actual OpenFOAM version used for each validation run is recorded in run metadata; the GitHub Actions smoke workflow records the container image in `.github/workflows/reproduce.yml`.
"""

REPRODUCTION_SECTION = """## Reproduction

Local execution requires an OpenFOAM-enabled shell.

Run the base smoke case:

```bash
bash scripts/run_cavity.sh
```

Run the included base mesh resolutions explicitly:

```bash
MESH_RESOLUTION=20 bash scripts/run_cavity.sh
MESH_RESOLUTION=40 bash scripts/run_cavity.sh
```

Run the full validation-v2 workflow locally:

```bash
python scripts/run_cavity_validation_v2.py --overwrite --resolutions 20 40 80 160 --end-times 20 30 40 50 60 70 80
```

Export the lightweight public validation-v2 summaries and figures:

```bash
python scripts/export_validation_v2_public.py
```

Check the required public smoke outputs:

```bash
python scripts/check_outputs.py
```

`runs/` stores untracked local solver fields and logs. The validation-v2 run directory `runs/cavity_validation_v2/` is not tracked by git. The public lightweight validation-v2 results are exported to `results/public/cavity_validation_v2/` and `figures/cavity_validation_v2/`.

The cleanup script removes generated OpenFOAM and post-processing outputs for the base case:

```bash
bash scripts/clean_case.sh
```
"""

REFERENCE_DATA_SECTION = """## Reference Data

The validation-v2 centerline comparisons use the Re=100 reference data from Ghia, Ghia & Shin (1982):

U. Ghia, K. N. Ghia, and C. T. Shin, "High-Re Solutions for Incompressible Flow Using the Navier-Stokes Equations and a Multigrid Method," Journal of Computational Physics 48(3), 387-411. DOI: `10.1016/0021-9991(82)90058-4`.

Reference metadata, source links, and checksums are recorded in `data/reference/source.json`. The repository stores only lightweight tabulated reference values and metadata, not the full paper text.
"""

LIMITATIONS_SECTION = """## Limitations

- The validation scope is limited to the two-dimensional laminar `Re = 100` lid-driven cavity and 17 centerline sample points per profile.
- The validation-v2 conclusions should not be extrapolated to turbulence, complex geometries, industrial meshes, or production CFD workflows.
- The OpenFOAM-11 GitHub Actions workflow is a smoke reproduction for the base case; the full validation-v2 evidence comes from local OpenFOAM-10 runs.
- ParaView is recommended for richer field inspection.
"""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def fmt(value: str | float) -> str:
    numeric = float(value)
    if numeric == 0:
        return "0"
    if abs(numeric) < 1e-4 or abs(numeric) >= 1e4:
        return f"{numeric:.3e}"
    return f"{numeric:.6g}"


def copy_public_files(run_root: Path, public_results: Path, public_figures: Path) -> None:
    public_results.mkdir(parents=True, exist_ok=True)
    public_figures.mkdir(parents=True, exist_ok=True)
    for name in CSV_EXPORTS:
        if name == "manifest.csv":
            write_public_manifest(read_csv(run_root / name), public_results / name)
        else:
            shutil.copy2(run_root / name, public_results / name)
    for name in FIGURE_EXPORTS:
        shutil.copy2(run_root / "figures" / name, public_figures / name)


def write_public_manifest(manifest_rows: list[dict[str, str]], output_path: Path) -> None:
    rows = []
    for row in manifest_rows:
        resolution = row["resolution"]
        rows.append(
            {
                "resolution": resolution,
                "status": row["status"],
                "run_id": f"N{resolution}",
            }
        )
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["resolution", "status", "run_id"])
        writer.writeheader()
        writer.writerows(rows)


def order_range(self_rows: list[dict[str, str]]) -> tuple[float, float]:
    values = []
    for row in self_rows:
        values.append(float(row["p_20_40_80"]))
        values.append(float(row["p_40_80_160"]))
    return min(values), max(values)


def rmse_is_strictly_monotonic(summary_rows: list[dict[str, str]], field: str) -> bool:
    values = [float(row[field]) for row in sorted(summary_rows, key=lambda item: int(item["resolution"]))]
    return all(a > b for a, b in zip(values, values[1:]))


def all_quality_gates_passed(quality_rows: list[dict[str, str]]) -> bool:
    return all(
        row["status"] == "completed"
        and row["courant_gate_passed"] == "True"
        and row["steady_gate_passed"] == "True"
        and row["exact_sample_gate_passed"] == "True"
        and row["quality_gate_passed"] == "True"
        for row in quality_rows
    )


def grid_differences_decrease(self_rows: list[dict[str, str]]) -> bool:
    return all(row["differences_decrease"] == "True" for row in self_rows)


def build_readme_section(
    summary_rows: list[dict[str, str]],
    self_rows: list[dict[str, str]],
    quality_rows: list[dict[str, str]],
) -> str:
    min_order, max_order = order_range(self_rows)
    all_quality = all_quality_gates_passed(quality_rows)
    differences_decrease = grid_differences_decrease(self_rows)
    rmse_monotonic = rmse_is_strictly_monotonic(summary_rows, "RMSE_U") and rmse_is_strictly_monotonic(
        summary_rows,
        "RMSE_V",
    )
    quality_sentence = (
        "All four grids pass the Courant, fixed-5 steady-state, solver-integrity, and exact-sampling quality gates."
        if all_quality
        else "At least one grid does not pass the Courant, fixed-5 steady-state, solver-integrity, and exact-sampling quality gates."
    )
    decrease_sentence = (
        "The centerline grid-to-grid differences continuously decrease across the `N20 -> N40`, `N40 -> N80`, and `N80 -> N160` comparisons."
        if differences_decrease
        else "The centerline grid-to-grid differences do not continuously decrease across every adjacent grid comparison."
    )
    rmse_sentence = (
        "The Ghia pointwise RMSE values are strictly monotonic across the grid sequence."
        if rmse_monotonic
        else "The Ghia pointwise RMSE values are not strictly monotonic across the grid sequence."
    )

    lines = [
        README_START,
        "## Validation V2 Results",
        "",
        "`runs/` contains the untracked full local solver fields and logs for validation-v2. `results/public/` and `figures/` contain the lightweight public CSV summaries and figures exported from those runs.",
        "",
        "The validation-v2 runs were generated with OpenFOAM-10. The GitHub Actions OpenFOAM-11 workflow is retained as a smoke reproduction path for the base cavity case, not as the source of the full grid-validation results.",
        "",
        "Validation v2 uses exact OpenFOAM `postProcess` point sampling with `cellPoint` interpolation at the 17 Ghia centerline coordinates for each profile. The nearest-cell centerline outputs are diagnostic only and are not used for the formal RMSE summary.",
        "",
        quality_sentence,
        "",
        decrease_sentence,
        "",
        f"The observed centerline self-convergence order ranges from approximately `{min_order:.2f}` to `{max_order:.2f}`.",
        "",
        f"{rmse_sentence} The validation claim is centerline self-convergence, not monotonic benchmark-error convergence.",
        "",
        "| N | RMSE_U | Linf_U | RMSE_V | Linf_V | samples U/V | max Co | fixed-5 L2 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(summary_rows, key=lambda item: int(item["resolution"])):
        lines.append(
            "| {N} | {RMSE_U} | {Linf_U} | {RMSE_V} | {Linf_V} | {samples} | {maxco} | {steady} |".format(
                N=row["resolution"],
                RMSE_U=fmt(row["RMSE_U"]),
                Linf_U=fmt(row["Linf_U"]),
                RMSE_V=fmt(row["RMSE_V"]),
                Linf_V=fmt(row["Linf_V"]),
                samples=f"{row['sample_count_U']}/{row['sample_count_V']}",
                maxco=fmt(row["observed_maxCo"]),
                steady=fmt(row["steady_relative_L2_over_5"]),
            )
        )

    lines.extend(
        [
            "",
            "Centerline self-convergence is computed on the same 17 exact sample points, not on the full two-dimensional velocity field.",
            "",
            "| Profile | d20_40 | d40_80 | d80_160 | p20_40_80 | p40_80_160 | Status |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in self_rows:
        lines.append(
            "| {profile} | {d20_40} | {d40_80} | {d80_160} | {p1} | {p2} | {status} |".format(
                profile=row["profile"],
                d20_40=fmt(row["d20_40"]),
                d40_80=fmt(row["d40_80"]),
                d80_160=fmt(row["d80_160"]),
                p1=fmt(row["p_20_40_80"]),
                p2=fmt(row["p_40_80_160"]),
                status=row["observed_order_status"],
            )
        )

    lines.extend(
        [
            "",
            "Public validation-v2 files:",
            "",
            "- `results/public/cavity_validation_v2/validation_summary.csv`",
            "- `results/public/cavity_validation_v2/self_convergence.csv`",
            "- `results/public/cavity_validation_v2/quality_gates.csv`",
            "- `results/public/cavity_validation_v2/manifest.csv`",
            "- `figures/cavity_validation_v2/exact_centerline_vs_ghia.png`",
            "- `figures/cavity_validation_v2/reference_rmse_by_grid.png`",
            "- `figures/cavity_validation_v2/grid_to_grid_difference.png`",
            "- `figures/cavity_validation_v2/observed_order.png`",
            "- `figures/cavity_validation_v2/error_vs_cost.png`",
            "",
            "![Validation v2 exact centerlines](figures/cavity_validation_v2/exact_centerline_vs_ghia.png)",
            "",
            "![Validation v2 grid differences](figures/cavity_validation_v2/grid_to_grid_difference.png)",
            "",
            "![Validation v2 observed order](figures/cavity_validation_v2/observed_order.png)",
            "",
            "![Validation v2 error versus cost](figures/cavity_validation_v2/error_vs_cost.png)",
            README_END,
        ]
    )
    return "\n".join(lines) + "\n"


def replace_title_and_opening(text: str) -> str:
    marker = "## What This Project Demonstrates"
    if marker not in text:
        raise ValueError("README.md does not contain the What This Project Demonstrates section marker.")
    _, rest = text.split(marker, 1)
    return README_INTRO.rstrip() + "\n\n" + marker + rest


def replace_section(text: str, heading: str, replacement: str) -> str:
    marker = f"## {heading}\n"
    if marker not in text:
        raise ValueError(f"README.md does not contain the {heading} section marker.")
    before, rest = text.split(marker, 1)
    next_heading = rest.find("\n## ")
    if next_heading == -1:
        return before.rstrip() + "\n\n" + replacement.rstrip() + "\n"
    return before.rstrip() + "\n\n" + replacement.rstrip() + "\n" + rest[next_heading:]


def replace_or_insert_section_before(
    text: str,
    heading: str,
    replacement: str,
    before_heading: str,
) -> str:
    if f"## {heading}\n" in text:
        return replace_section(text, heading, replacement)
    marker = f"## {before_heading}\n"
    if marker not in text:
        raise ValueError(f"README.md does not contain the {before_heading} section marker.")
    before, after = text.split(marker, 1)
    return before.rstrip() + "\n\n" + replacement.rstrip() + "\n\n" + marker + after


def remove_section_if_present(text: str, heading: str) -> str:
    marker = f"## {heading}\n"
    if marker not in text:
        return text
    before, rest = text.split(marker, 1)
    next_heading = rest.find("\n## ")
    if next_heading == -1:
        return before.rstrip() + "\n"
    return before.rstrip() + "\n" + rest[next_heading:]


def normalize_public_wording(text: str) -> str:
    replacements = {
        "- Legacy nearest-cell centerline extraction for smoke-run diagnostics.": (
            "- Nearest-cell centerline extraction for smoke-run diagnostics."
        ),
        "The legacy nearest-cell centerline outputs are diagnostic only": (
            "The nearest-cell centerline outputs are diagnostic only"
        ),
        "store legacy nearest-cell centerline velocity profiles": (
            "store nearest-cell centerline velocity profiles"
        ),
        "Legacy smoke-run centerline profiles use nearest-cell extraction": (
            "Smoke-run centerline profiles use nearest-cell extraction"
        ),
        "the legacy `sample` command": "the OpenFOAM `sample` command",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def update_readme(readme_path: Path, generated_section: str) -> None:
    text = readme_path.read_text(encoding="utf-8")
    text = replace_title_and_opening(text)
    if README_START in text and README_END in text:
        before, rest = text.split(README_START, 1)
        _, after = rest.split(README_END, 1)
        text = before.rstrip() + "\n\n" + generated_section + after.lstrip()
    else:
        marker = "## Smoke Reproduction Outputs\n"
        if marker not in text:
            raise ValueError("README.md does not contain the Smoke Reproduction Outputs section marker.")
        before, after = text.split(marker, 1)
        text = before.rstrip() + "\n\n" + generated_section + "\n" + marker + after
    text = replace_section(text, "Physical Setup", PHYSICAL_SETUP_SECTION)
    text = replace_section(text, "Mesh", MESH_SECTION)
    text = replace_section(text, "Reproduction", REPRODUCTION_SECTION)
    text = replace_or_insert_section_before(text, "Reference Data", REFERENCE_DATA_SECTION, "Governing Equations")
    text = replace_section(text, "Limitations", LIMITATIONS_SECTION)
    text = remove_section_if_present(text, "Legacy Grid-Validation Diagnostics")
    text = normalize_public_wording(text)
    readme_path.write_text(text, encoding="utf-8")


def export_validation_v2(
    run_root: Path = RUN_ROOT,
    public_results: Path = PUBLIC_RESULTS,
    public_figures: Path = PUBLIC_FIGURES,
    readme_path: Path = README,
) -> None:
    summary_rows = read_csv(run_root / "validation_summary.csv")
    self_rows = read_csv(run_root / "self_convergence.csv")
    quality_rows = read_csv(run_root / "quality_gates.csv")
    copy_public_files(run_root, public_results, public_figures)
    update_readme(readme_path, build_readme_section(summary_rows, self_rows, quality_rows))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--public-results", type=Path, default=PUBLIC_RESULTS)
    parser.add_argument("--public-figures", type=Path, default=PUBLIC_FIGURES)
    parser.add_argument("--readme", type=Path, default=README)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    export_validation_v2(args.run_root, args.public_results, args.public_figures, args.readme)
    print(f"Exported validation-v2 public files to {args.public_results} and {args.public_figures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
