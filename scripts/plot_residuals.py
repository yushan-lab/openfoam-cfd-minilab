#!/usr/bin/env python3
"""Parse icoFoam residual logs and plot initial residual histories."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


TIME_RE = re.compile(r"^Time = ([0-9.+\-eE]+)")
RESIDUAL_RE = re.compile(
    r"Solving for ([A-Za-z0-9_]+), Initial residual = ([0-9.+\-eE]+), "
    r"Final residual = ([0-9.+\-eE]+), No Iterations ([0-9]+)"
)
CONTINUITY_RE = re.compile(
    r"time step continuity errors : sum local = ([0-9.+\-eE]+), "
    r"global = ([0-9.+\-eE]+), cumulative = ([0-9.+\-eE]+)"
)


@dataclass(frozen=True)
class ResidualEntry:
    time: float
    field: str
    initial_residual: float
    final_residual: float
    iterations: int


@dataclass(frozen=True)
class ContinuityEntry:
    time: float
    sum_local: float
    global_error: float
    cumulative: float


@dataclass(frozen=True)
class SolverDiagnostics:
    residuals: list[ResidualEntry]
    continuity_errors: list[ContinuityEntry]
    failure_reasons: list[str]

    @property
    def has_failure(self) -> bool:
        return bool(self.failure_reasons)


def parse_residuals(log_text: str) -> dict[str, list[tuple[float, float]]]:
    """Return initial residuals keyed by solved field name."""
    series: dict[str, list[tuple[float, float]]] = {}
    for entry in parse_solver_log(log_text).residuals:
        series.setdefault(entry.field, []).append((entry.time, entry.initial_residual))
    return series


def parse_solver_log(log_text: str) -> SolverDiagnostics:
    current_time: float | None = None
    residuals: list[ResidualEntry] = []
    continuity_errors: list[ContinuityEntry] = []
    failure_reasons: list[str] = []
    for line in log_text.splitlines():
        lower_line = line.lower()
        if "floating point exception" in lower_line and "trapping" not in lower_line:
            failure_reasons.append("Floating point exception")
        if re.search(r"(?<![a-z])nan(?![a-z])", lower_line):
            failure_reasons.append("NaN detected")
        if re.search(r"\b(?:diverged|divergence|diverging)\b", lower_line):
            failure_reasons.append("Solver divergence reported")

        time_match = TIME_RE.search(line.strip())
        if time_match:
            current_time = float(time_match.group(1))
            continue

        residual_match = RESIDUAL_RE.search(line)
        if residual_match and current_time is not None:
            residuals.append(
                ResidualEntry(
                    time=current_time,
                    field=residual_match.group(1),
                    initial_residual=float(residual_match.group(2)),
                    final_residual=float(residual_match.group(3)),
                    iterations=int(residual_match.group(4)),
                )
            )

        continuity_match = CONTINUITY_RE.search(line)
        if continuity_match and current_time is not None:
            continuity_errors.append(
                ContinuityEntry(
                    time=current_time,
                    sum_local=float(continuity_match.group(1)),
                    global_error=float(continuity_match.group(2)),
                    cumulative=float(continuity_match.group(3)),
                )
            )

    return SolverDiagnostics(
        residuals=residuals,
        continuity_errors=continuity_errors,
        failure_reasons=sorted(set(failure_reasons)),
    )


def write_residual_csv(path: Path, series: dict[str, list[tuple[float, float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "field", "initial_residual"])
        for field in sorted(series):
            for time_value, residual in series[field]:
                writer.writerow([time_value, field, residual])


def write_residual_entries_csv(path: Path, residuals: list[ResidualEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "field", "initial_residual", "final_residual", "iterations"])
        for entry in residuals:
            writer.writerow(
                [
                    entry.time,
                    entry.field,
                    entry.initial_residual,
                    entry.final_residual,
                    entry.iterations,
                ]
            )


def write_continuity_csv(path: Path, continuity_errors: list[ContinuityEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "sum_local", "global_error", "cumulative"])
        for entry in continuity_errors:
            writer.writerow([entry.time, entry.sum_local, entry.global_error, entry.cumulative])


def write_solver_summary_json(path: Path, diagnostics: SolverDiagnostics) -> None:
    final_residuals: dict[str, dict[str, float | int]] = {}
    for entry in diagnostics.residuals:
        final_residuals[entry.field] = {
            "time": entry.time,
            "initial_residual": entry.initial_residual,
            "final_residual": entry.final_residual,
            "iterations": entry.iterations,
        }

    payload = {
        "has_failure": diagnostics.has_failure,
        "failure_reasons": diagnostics.failure_reasons,
        "final_residuals": final_residuals,
        "final_continuity_error": (
            {
                "time": diagnostics.continuity_errors[-1].time,
                "sum_local": diagnostics.continuity_errors[-1].sum_local,
                "global_error": diagnostics.continuity_errors[-1].global_error,
                "cumulative": diagnostics.continuity_errors[-1].cumulative,
            }
            if diagnostics.continuity_errors
            else None
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def plot_residuals(path: Path, series: dict[str, list[tuple[float, float]]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not series:
        raise ValueError("No residual entries found in the solver log.")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4.5))

    for field in sorted(series):
        values = series[field]
        times = [item[0] for item in values]
        residuals = [item[1] for item in values]
        ax.semilogy(times, residuals, label=field, linewidth=1.6)

    ax.set_xlabel("Time")
    ax.set_ylabel("Initial residual")
    ax.set_title("icoFoam residual history")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def describe_missing_log(path: Path) -> str:
    lines = [f"Missing residual log: {path}"]
    log_dir = path.parent
    if log_dir.exists():
        files = sorted(item for item in log_dir.rglob("*") if item.is_file())
        lines.append(f"Files currently under {log_dir}:")
        if files:
            lines.extend(f"  - {item}" for item in files)
        else:
            lines.append("  (no files found)")
    else:
        lines.append(f"Log directory does not exist: {log_dir}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=Path("results/logs/icoFoam.log"))
    parser.add_argument("--output", type=Path, default=Path("figures/cavity_residuals.png"))
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--continuity-csv", type=Path, default=None)
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    if not args.log.exists():
        print(describe_missing_log(args.log), file=sys.stderr)
        return 1

    log_text = args.log.read_text()
    diagnostics = parse_solver_log(log_text)
    series = parse_residuals(log_text)
    if not series:
        raise SystemExit(f"No residuals found in {args.log}")

    if args.csv is not None:
        write_residual_entries_csv(args.csv, diagnostics.residuals)
    if args.continuity_csv is not None:
        write_continuity_csv(args.continuity_csv, diagnostics.continuity_errors)
    if args.summary_json is not None:
        write_solver_summary_json(args.summary_json, diagnostics)
    plot_residuals(args.output, series)
    print(f"Wrote {args.output}")
    if args.csv is not None:
        print(f"Wrote {args.csv}")
    if args.continuity_csv is not None:
        print(f"Wrote {args.continuity_csv}")
    if args.summary_json is not None:
        print(f"Wrote {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
