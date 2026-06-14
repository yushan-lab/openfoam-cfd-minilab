#!/usr/bin/env python3
"""Fail unless required OpenFOAM reproduction outputs exist and are non-empty."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


REQUIRED_OUTPUTS = [
    "results/logs/blockMesh.log",
    "results/logs/checkMesh.log",
    "results/logs/icoFoam.log",
    "results/residuals.csv",
    "results/centerline_u.csv",
    "results/centerline_v.csv",
    "figures/cavity_residuals.png",
    "figures/cavity_centerline_profiles.png",
]

OPTIONAL_OUTPUTS = [
    "figures/cavity_velocity_magnitude.png",
]


@dataclass(frozen=True)
class OutputReport:
    missing_required: list[str]
    empty_required: list[str]
    missing_optional: list[str]
    empty_optional: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.empty_required


def check_outputs(root: Path) -> OutputReport:
    missing_required: list[str] = []
    empty_required: list[str] = []
    missing_optional: list[str] = []
    empty_optional: list[str] = []

    for relative_path in REQUIRED_OUTPUTS:
        path = root / relative_path
        if not path.exists():
            missing_required.append(relative_path)
        elif path.stat().st_size == 0:
            empty_required.append(relative_path)

    for relative_path in OPTIONAL_OUTPUTS:
        path = root / relative_path
        if not path.exists():
            missing_optional.append(relative_path)
        elif path.stat().st_size == 0:
            empty_optional.append(relative_path)

    return OutputReport(
        missing_required=missing_required,
        empty_required=empty_required,
        missing_optional=missing_optional,
        empty_optional=empty_optional,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    report = check_outputs(args.root)
    if report.missing_required:
        print("Missing required outputs:")
        for path in report.missing_required:
            print(f"  - {path}")
    if report.empty_required:
        print("Empty required outputs:")
        for path in report.empty_required:
            print(f"  - {path}")
    if report.missing_optional:
        print("Missing optional outputs:")
        for path in report.missing_optional:
            print(f"  - {path}")
    if report.empty_optional:
        print("Empty optional outputs:")
        for path in report.empty_optional:
            print(f"  - {path}")

    if not report.ok:
        return 1

    print("All required OpenFOAM reproduction outputs are present and non-empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
