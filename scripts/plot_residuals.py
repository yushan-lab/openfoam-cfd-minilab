#!/usr/bin/env python3
"""Parse icoFoam residual logs and plot initial residual histories."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re
import sys


TIME_RE = re.compile(r"^Time = ([0-9.+\-eE]+)")
RESIDUAL_RE = re.compile(
    r"Solving for ([A-Za-z0-9_]+), Initial residual = ([0-9.+\-eE]+),"
)


def parse_residuals(log_text: str) -> dict[str, list[tuple[float, float]]]:
    """Return initial residuals keyed by solved field name."""
    current_time: float | None = None
    series: dict[str, list[tuple[float, float]]] = {}

    for line in log_text.splitlines():
        time_match = TIME_RE.search(line.strip())
        if time_match:
            current_time = float(time_match.group(1))
            continue

        residual_match = RESIDUAL_RE.search(line)
        if residual_match and current_time is not None:
            field = residual_match.group(1)
            residual = float(residual_match.group(2))
            series.setdefault(field, []).append((current_time, residual))

    return series


def write_residual_csv(path: Path, series: dict[str, list[tuple[float, float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "field", "initial_residual"])
        for field in sorted(series):
            for time_value, residual in series[field]:
                writer.writerow([time_value, field, residual])


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
    args = parser.parse_args()

    if not args.log.exists():
        print(describe_missing_log(args.log), file=sys.stderr)
        return 1

    log_text = args.log.read_text()
    series = parse_residuals(log_text)
    if not series:
        raise SystemExit(f"No residuals found in {args.log}")

    if args.csv is not None:
        write_residual_csv(args.csv, series)
    plot_residuals(args.output, series)
    print(f"Wrote {args.output}")
    if args.csv is not None:
        print(f"Wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
