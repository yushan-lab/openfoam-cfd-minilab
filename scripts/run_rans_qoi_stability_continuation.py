#!/usr/bin/env python3
"""Run the fixed +300 iteration RANS QoI stability continuation cases."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import rans_pitzdaily_formal_tools as tools  # noqa: E402


MODELS = ("kEpsilon", "kOmegaSST")
CONTINUATION_PROFILE = "continuation_common"


def ensure_within_runs(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "runs/rans_pitzdaily_formal_v2/stability_continuation").resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"Refusing continuation output outside {allowed}: {resolved}")
    return resolved


def numeric_latest(case_dir: Path) -> float:
    latest = tools.latest_numeric_time(case_dir)
    if latest is None:
        raise ValueError(f"No numeric time directories found in {case_dir}")
    return float(latest)


def format_time(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def replace_dictionary_entry(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^(\s*{re.escape(key)}\s+)[^;]+;"
    replacement = rf"\g<1>{value};"
    updated, count = re.subn(pattern, replacement, text)
    if count == 0:
        updated = text.rstrip() + f"\n\n{key:<16}{value};\n"
    return updated


def configure_control_dict(
    path: Path,
    model: str,
    final_time: float,
    additional_iterations: int,
    write_interval: int,
) -> str:
    continuation_end = final_time + additional_iterations
    text = path.read_text(encoding="utf-8", errors="ignore")
    continuation_end_text = format_time(continuation_end)
    for key, value in [
        ("startFrom", "latestTime"),
        ("startTime", format_time(final_time)),
        ("stopAt", "endTime"),
        ("endTime", continuation_end_text),
        ("writeControl", "timeStep"),
        ("writeInterval", "1"),
    ]:
        text = replace_dictionary_entry(text, key, value)
    path.write_text(text, encoding="utf-8")
    return continuation_end_text


def disable_residual_control(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    changed = False
    while True:
        match = re.search(r"(?m)^\s*residualControl\s*\{", text)
        if match is None:
            break
        open_index = text.find("{", match.start())
        close_index = tools._find_matching_brace(text, open_index)
        line_start = text.rfind("\n", 0, match.start()) + 1
        end = close_index + 1
        if end < len(text) and text[end] == "\n":
            end += 1
        indent = re.match(r"\s*", text[match.start() : match.end()]).group(0)
        text = (
            text[:line_start]
            + f"{indent}// residualControl disabled for fixed 300-iteration stability continuation.\n"
            + text[end:]
        )
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def copy_case(source: Path, destination: Path, overwrite: bool) -> None:
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"Continuation case already exists: {destination}")
        resolved = ensure_within_runs(destination)
        shutil.rmtree(resolved)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def run_command_to_log(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="ignore") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, text=True, check=False)
    return completed.returncode


def run_best_effort_postprocess(case_dir: Path, log_dir: Path) -> None:
    postprocess_commands = [
        ("postProcess_yPlus.log", ["simpleFoam", "-case", str(case_dir), "-postProcess", "-latestTime", "-func", "yPlus"]),
        (
            "postProcess_wallShearStress.log",
            ["simpleFoam", "-case", str(case_dir), "-postProcess", "-latestTime", "-func", "wallShearStress"],
        ),
        ("postProcess_writeCellCentres.log", ["postProcess", "-case", str(case_dir), "-latestTime", "-func", "writeCellCentres"]),
        ("postProcess_writeCellVolumes.log", ["postProcess", "-case", str(case_dir), "-latestTime", "-func", "writeCellVolumes"]),
    ]
    for patch in ["inlet", "outlet"]:
        postprocess_commands.extend(
            [
                (
                    f"postProcess_patchFlowRate_{patch}.log",
                    ["postProcess", "-case", str(case_dir), "-latestTime", "-func", f"patchFlowRate(phi,patch={patch})"],
                ),
                (
                    f"postProcess_patchAverage_p_{patch}.log",
                    ["postProcess", "-case", str(case_dir), "-latestTime", "-func", f"patchAverage(p,patch={patch})"],
                ),
            ]
        )
    for log_name, command in postprocess_commands:
        status = run_command_to_log(command, log_dir / log_name)
        with (log_dir / log_name).open("a", encoding="utf-8") as handle:
            handle.write(f"\npostprocess status {status} for {' '.join(command)}\n")


def run_model_continuation(case_dir: Path, model: str) -> dict[str, Any]:
    log_dir = case_dir / "logs"
    result_dir = case_dir / "results"
    log_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    start_epoch = int(time.time())
    simple_status = run_command_to_log(["simpleFoam", "-case", str(case_dir)], log_dir / "simpleFoam_continuation.log")
    tools.run_command(
        [
            sys.executable,
            str(ROOT / "scripts/parse_simplefoam_log.py"),
            "--log",
            str(log_dir / "simpleFoam_continuation.log"),
            "--results-dir",
            str(result_dir),
        ]
    )
    run_best_effort_postprocess(case_dir, log_dir)
    end_epoch = int(time.time())
    summary = tools.postprocess_case(
        case_dir=case_dir,
        model=model,
        profile_name=CONTINUATION_PROFILE,
        wall_clock_seconds=end_epoch - start_epoch,
        solver_exit_code=simple_status,
        pair_audit_passed=True,
        run_start_epoch=start_epoch,
        run_end_epoch=end_epoch,
    )
    return {
        "model": model,
        "case_dir": case_dir.as_posix(),
        "solver_exit_code": simple_status,
        "final_time": summary.get("final_time"),
        "status": summary.get("status"),
        "quality_gate_status": summary.get("quality_gate_status"),
    }


def retain_time_dir(value: float, initial_final: float, continuation_end: float, retained_interval: int) -> bool:
    tolerance = 1e-9
    if value <= initial_final + tolerance:
        return True
    if abs(value - continuation_end) <= tolerance:
        return True
    if abs(value - (continuation_end - 100.0)) <= tolerance:
        return True
    if retained_interval > 0:
        nearest = round(value / retained_interval) * retained_interval
        if abs(value - nearest) <= tolerance:
            return True
    return False


def prune_continuation_time_dirs(
    case_dir: Path,
    initial_final: float,
    continuation_end: float,
    retained_interval: int,
) -> list[str]:
    removed: list[str] = []
    case_root = case_dir.resolve()
    for path in case_dir.iterdir():
        if not path.is_dir() or path.name == "0":
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if retain_time_dir(value, initial_final, continuation_end, retained_interval):
            continue
        resolved = path.resolve()
        if case_root not in resolved.parents:
            raise ValueError(f"Refusing to remove time directory outside case: {resolved}")
        shutil.rmtree(resolved)
        removed.append(path.name)
    return sorted(removed, key=float)


def write_manifest(profile_root: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "model",
        "profile_name",
        "status",
        "iterations",
        "output_root",
        "wall_clock_seconds",
        "git_commit",
        "openfoam_version",
        "pair_audit_passed",
    ]
    output = profile_root / "manifest.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_selection_summary(output_root: Path, model_rows: list[dict[str, Any]]) -> None:
    statuses = {row["model"]: row.get("status") for row in model_rows}
    quality = {row["model"]: row.get("quality_gate_status") for row in model_rows}
    selection = {
        "selected_profile": CONTINUATION_PROFILE,
        "profiles_attempted": [CONTINUATION_PROFILE],
        "fallback_triggered": False,
        "model_statuses": statuses,
        "quality_gate_statuses": quality,
        "selection_reason": "fixed shared +300-iteration QoI stability continuation after initial stability gates failed",
        "selection_basis": "same copied selected cases, unchanged mesh, boundary conditions, schemes, and relaxation factors",
        "comparison_status": "stability_continuation",
    }
    selection_dir = output_root / "selected"
    selection_dir.mkdir(parents=True, exist_ok=True)
    (selection_dir / "selection_summary.json").write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_continuation(
    source_root: Path,
    output_root: Path,
    selected_profile: str,
    additional_iterations: int,
    write_interval: int,
    overwrite: bool,
) -> dict[str, Any]:
    output_root = ensure_within_runs(output_root)
    profile_root = output_root / CONTINUATION_PROFILE
    report_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    for model in MODELS:
        source_case = source_root / selected_profile / model
        destination = profile_root / model
        copy_case(source_case, destination, overwrite=overwrite)
        initial_final = numeric_latest(destination)
        continuation_end = configure_control_dict(
            destination / "system/controlDict",
            model,
            initial_final,
            additional_iterations,
            write_interval,
        )
        disabled = disable_residual_control(destination / "system/fvSolution")
        model_report = run_model_continuation(destination, model)
        removed_times = prune_continuation_time_dirs(
            destination,
            initial_final,
            float(continuation_end),
            write_interval,
        )
        model_report.update(
            {
                "initial_final_time": format_time(initial_final),
                "configured_end_time": continuation_end,
                "residual_control_disabled": disabled,
                "additional_iterations": additional_iterations,
                "write_interval": write_interval,
                "solver_write_interval": 1,
                "retained_checkpoint_interval": write_interval,
                "removed_intermediate_time_directories": removed_times,
            }
        )
        report_rows.append(model_report)
        summary = json.loads((destination / "results/solver_summary.json").read_text(encoding="utf-8"))
        manifest_rows.append(
            tools.manifest_row(
                model=model,
                status=summary.get("status"),
                iterations=summary.get("actual_iterations"),
                output_root=destination,
                wall_clock_seconds=summary.get("wall_clock_seconds"),
                git_commit=tools.git_commit(),
                openfoam_version=tools.openfoam_version(),
                pair_audit_passed=True,
                profile_name=CONTINUATION_PROFILE,
            )
        )
    write_manifest(profile_root, manifest_rows)
    write_selection_summary(output_root, report_rows)
    final_audit = tools.generate_final_audit(output_root, CONTINUATION_PROFILE)
    report = {
        "source_root": source_root.as_posix(),
        "source_profile": selected_profile,
        "output_root": output_root.as_posix(),
        "continuation_profile": CONTINUATION_PROFILE,
        "models": report_rows,
        "final_audit": final_audit.get("output_files", {}),
    }
    report_path = output_root / "continuation_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "runs/rans_pitzdaily_formal_v2")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runs/rans_pitzdaily_formal_v2/stability_continuation",
    )
    parser.add_argument("--selected-profile", default="conservative_common")
    parser.add_argument("--additional-iterations", type=int, default=300)
    parser.add_argument("--write-interval", type=int, default=100)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_continuation(
        source_root=args.source_root,
        output_root=args.output_root,
        selected_profile=args.selected_profile,
        additional_iterations=args.additional_iterations,
        write_interval=args.write_interval,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    failures = [row for row in report["models"] if row.get("solver_exit_code") != 0]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
