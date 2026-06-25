#!/usr/bin/env python3
"""Parse OpenFOAM simpleFoam logs into CSV diagnostics and a JSON summary."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Any


TIME_RE = re.compile(r"^Time =\s*([0-9.+\-Ee]+)")
SOLVER_RE = re.compile(
    r"Solving for\s+([^,]+),\s+Initial residual =\s*([0-9.+\-Ee]+),\s+"
    r"Final residual =\s*([0-9.+\-Ee]+),\s+No Iterations\s+([0-9]+)"
)
CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:\s*sum local =\s*([0-9.+\-Ee]+),\s+"
    r"global =\s*([0-9.+\-Ee-]+),\s+cumulative =\s*([0-9.+\-Ee-]+)"
)


def parse_solver_log(text: str) -> dict[str, Any]:
    residuals: list[dict[str, Any]] = []
    continuity: list[dict[str, Any]] = []
    current_iteration: int | None = None
    converged = False
    ended = False

    for line in text.splitlines():
        time_match = TIME_RE.search(line.strip())
        if time_match:
            current_iteration = int(float(time_match.group(1)))
            continue

        solver_match = SOLVER_RE.search(line)
        if solver_match:
            residuals.append(
                {
                    "iteration": current_iteration,
                    "field": solver_match.group(1).strip(),
                    "initial_residual": float(solver_match.group(2)),
                    "final_residual": float(solver_match.group(3)),
                    "linear_iterations": int(solver_match.group(4)),
                }
            )
            continue

        continuity_match = CONTINUITY_RE.search(line)
        if continuity_match:
            continuity.append(
                {
                    "iteration": current_iteration,
                    "sum_local": float(continuity_match.group(1)),
                    "global": float(continuity_match.group(2)),
                    "cumulative": float(continuity_match.group(3)),
                }
            )
            continue

        if "SIMPLE solution converged" in line:
            converged = True
        if line.strip() == "End":
            ended = True

    final_residuals: dict[str, dict[str, Any]] = {}
    for row in residuals:
        final_residuals[row["field"]] = {
            "iteration": row["iteration"],
            "initial_residual": row["initial_residual"],
            "final_residual": row["final_residual"],
            "linear_iterations": row["linear_iterations"],
        }

    lower = text.lower()
    has_nan = bool(re.search(r"\bnan\b", lower))
    has_fpe = any(
        "floating point exception" in line.lower() and "trapping" not in line.lower()
        for line in text.splitlines()
    )
    has_fatal = "foam fatal error" in lower or "fatal error" in lower
    has_unknown_model = "unknown turbulence model" in lower or "unknown model" in lower
    has_missing_field = (
        "cannot find file" in lower
        or "cannot find field" in lower
        or "missing field" in lower
        or "no such file" in lower
    )
    actual_iterations = max([row["iteration"] for row in residuals if row["iteration"] is not None] or [0])
    return {
        "actual_iterations": actual_iterations,
        "residuals": residuals,
        "continuity_errors": continuity,
        "final_residuals": final_residuals,
        "final_continuity_error": continuity[-1] if continuity else None,
        "fields_solved": sorted(final_residuals),
        "converged": converged,
        "ended": ended,
        "has_nan": has_nan,
        "has_floating_point_exception": has_fpe,
        "has_fatal_error": has_fatal,
        "has_unknown_model": has_unknown_model,
        "has_missing_field": has_missing_field,
        "failed": has_nan or has_fpe or has_fatal or has_unknown_model or has_missing_field or not ended,
    }


def write_residuals_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "iteration",
                "field",
                "initial_residual",
                "final_residual",
                "linear_iterations",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_continuity_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["iteration", "sum_local", "global", "cumulative"])
        writer.writeheader()
        writer.writerows(rows)


def parse_and_write(log_path: Path, results_dir: Path) -> dict[str, Any]:
    summary = parse_solver_log(log_path.read_text(errors="ignore"))
    results_dir.mkdir(parents=True, exist_ok=True)
    write_residuals_csv(results_dir / "residuals.csv", summary["residuals"])
    write_continuity_csv(results_dir / "continuity_errors.csv", summary["continuity_errors"])
    summary_for_json = {key: value for key, value in summary.items() if key not in {"residuals", "continuity_errors"}}
    (results_dir / "solver_summary.json").write_text(
        json.dumps(summary_for_json, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary_for_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = parse_and_write(args.log, args.results_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
