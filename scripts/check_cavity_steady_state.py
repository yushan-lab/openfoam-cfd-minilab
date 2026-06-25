#!/usr/bin/env python3
"""Check steady-state change between the final two OpenFOAM velocity fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from postprocess_cavity import read_internal_vector_field, reconstruct_structured_cell_centres


Vector = tuple[float, float, float]


def relative_l2_change(latest: Sequence[Vector], previous: Sequence[Vector]) -> float:
    if len(latest) != len(previous):
        raise ValueError("Velocity fields must have the same number of cells.")
    diff_sq = 0.0
    latest_sq = 0.0
    for latest_vec, previous_vec in zip(latest, previous):
        for latest_value, previous_value in zip(latest_vec, previous_vec):
            diff_sq += (latest_value - previous_value) ** 2
            latest_sq += latest_value**2
    return diff_sq**0.5 / max(latest_sq**0.5, 1e-12)


def find_numeric_time_dirs(case_dir: Path) -> list[Path]:
    times: list[tuple[float, Path]] = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            times.append((float(path.name), path))
        except ValueError:
            continue
    return [path for _, path in sorted(times)]


def check_steady_state(case_dir: Path, threshold: float = 1e-5) -> dict[str, object]:
    time_dirs = find_numeric_time_dirs(case_dir)
    if len(time_dirs) < 2:
        raise ValueError(f"Need at least two numeric time directories under {case_dir}.")

    expected_count = len(reconstruct_structured_cell_centres(case_dir / "system" / "blockMeshDict"))
    previous_time = time_dirs[-2]
    latest_time = time_dirs[-1]
    previous = read_internal_vector_field(previous_time / "U", expected_count=expected_count)
    latest = read_internal_vector_field(latest_time / "U", expected_count=expected_count)
    change = relative_l2_change(latest, previous)

    return {
        "converged": change <= threshold,
        "latest_time": latest_time.name,
        "previous_time": previous_time.name,
        "relative_L2_change": change,
        "threshold": threshold,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cases/lid_driven_cavity"))
    parser.add_argument("--threshold", type=float, default=1e-5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = check_steady_state(args.case, args.threshold)
    text = json.dumps(result, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
