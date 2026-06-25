#!/usr/bin/env python3
"""Prepare paired OpenFOAM-10 pitzDaily RANS model cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "cases/rans_pitzdaily"
MODELS = {"kEpsilon", "kOmegaSST"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def openfoam_version() -> str:
    if os.environ.get("WM_PROJECT_VERSION"):
        return os.environ["WM_PROJECT_VERSION"]
    if shutil.which("wsl.exe"):
        completed = subprocess.run(
            [
                "wsl.exe",
                "-d",
                "Ubuntu-22.04",
                "--",
                "bash",
                "-lc",
                "source /opt/openfoam10/etc/bashrc; echo $WM_PROJECT_VERSION",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        value = completed.stdout.strip()
        if value:
            return value
    return "10"


def ensure_clean_output(output: Path, overwrite: bool) -> None:
    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Refusing to overwrite non-empty output directory: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)


def copy_tree_contents(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if item.is_file():
            target = dst / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def set_end_time(control_dict: Path, max_iterations: int | None) -> None:
    if max_iterations is None:
        return
    text = control_dict.read_text(encoding="utf-8")
    text = re.sub(r"(^\s*endTime\s+)[^;]+;", rf"\g<1>{max_iterations};", text, flags=re.MULTILINE)
    control_dict.write_text(text, encoding="utf-8")


def generated_hashes(output: Path) -> dict[str, str]:
    ignored_parts = {"case_manifest.json", "constant/polyMesh", "logs", "results"}
    hashes: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(output).as_posix()
        if rel in ignored_parts or rel.startswith("logs/") or rel.startswith("results/"):
            continue
        hashes[rel] = sha256(path)
    return hashes


def parse_max_iterations(value: str | int | None) -> int | None:
    if value is None:
        return 50
    if isinstance(value, int):
        return value
    if value.lower() == "official":
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("--max-iterations must be positive or 'official'")
    return parsed


def prepare_case(
    model: str,
    output: Path,
    max_iterations: int | str | None = 50,
    overwrite: bool = False,
) -> dict[str, object]:
    if model not in MODELS:
        raise ValueError(f"Unsupported model {model!r}; expected one of {sorted(MODELS)}")
    if not SOURCE_ROOT.exists():
        raise FileNotFoundError(f"Missing source tree: {SOURCE_ROOT}")

    parsed_max_iterations = parse_max_iterations(max_iterations)
    ensure_clean_output(output, overwrite)

    copy_tree_contents(SOURCE_ROOT / "base", output)
    copy_tree_contents(SOURCE_ROOT / "models" / model, output)
    set_end_time(output / "system/controlDict", parsed_max_iterations)

    source_manifest = json.loads((SOURCE_ROOT / "case_source.json").read_text(encoding="utf-8"))
    hashes = generated_hashes(output)
    model_fields = sorted(path.name for path in (output / "0").iterdir() if path.is_file() and path.name not in {"U", "p"})
    manifest = {
        "model": model,
        "openfoam_distribution": "Foundation",
        "openfoam_version": openfoam_version(),
        "solver": "simpleFoam",
        "max_iterations": parsed_max_iterations if parsed_max_iterations is not None else "official",
        "source_hashes": source_manifest["source_file_sha256"],
        "source_case_hashes": source_manifest["generated_file_sha256"],
        "generated_case_hashes": hashes,
        "mesh_hash": hashes["system/blockMeshDict"],
        "common_hashes": {
            "system/blockMeshDict": hashes["system/blockMeshDict"],
            "system/controlDict": hashes["system/controlDict"],
            "system/fvSchemes": hashes["system/fvSchemes"],
            "system/fvSolution": hashes["system/fvSolution"],
            "constant/physicalProperties": hashes["constant/physicalProperties"],
            "0/U": hashes["0/U"],
            "0/p": hashes["0/p"],
        },
        "model_specific_fields": model_fields,
        "git_commit": git_commit(),
        "generated_at_utc": utc_now(),
    }
    (output / "case_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-iterations", default="50", help="Positive integer, or 'official' to keep tutorial endTime.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = prepare_case(args.model, args.output, args.max_iterations, args.overwrite)
    print(f"Prepared {manifest['model']} case at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
