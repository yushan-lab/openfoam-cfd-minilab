#!/usr/bin/env python3
"""Audit paired kEpsilon/kOmegaSST pitzDaily cases for setup fairness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


COMMON_FILES = [
    "system/blockMeshDict",
    "system/fvSchemes",
    "system/fvSolution",
    "system/controlDict",
    "constant/physicalProperties",
    "0/U",
    "0/p",
]

EXPECTED_MODEL_DIFFERENCES = [
    "constant/momentumTransport",
    "0/epsilon",
    "0/omega",
    "0/k",
    "0/nut",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_or_missing(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return sha256(path)


def all_case_files(case_dir: Path) -> set[str]:
    ignored_prefixes = (
        "constant/polyMesh/",
        "logs/",
        "results/",
        "postProcessing/",
        "VTK/",
    )
    ignored_names = {"case_manifest.json", "run_metadata.json"}
    files: set[str] = set()
    for path in case_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(case_dir).as_posix()
        if rel in ignored_names or rel.split("/", 1)[0].replace(".", "", 1).isdigit():
            continue
        if rel.startswith(ignored_prefixes):
            continue
        files.add(rel)
    return files


def audit_pair(k_epsilon_dir: Path, k_omega_sst_dir: Path, output: Path | None = None) -> dict[str, object]:
    common_hashes = {
        rel: {
            "kEpsilon": hash_or_missing(k_epsilon_dir / rel),
            "kOmegaSST": hash_or_missing(k_omega_sst_dir / rel),
        }
        for rel in COMMON_FILES
    }
    common_matches = {rel: values["kEpsilon"] == values["kOmegaSST"] for rel, values in common_hashes.items()}

    left_files = all_case_files(k_epsilon_dir)
    right_files = all_case_files(k_omega_sst_dir)
    all_files = left_files | right_files
    unexpected: list[str] = []
    for rel, matches in common_matches.items():
        if not matches:
            unexpected.append(rel)
    for rel in sorted(all_files):
        if rel in COMMON_FILES or rel in EXPECTED_MODEL_DIFFERENCES:
            continue
        if hash_or_missing(k_epsilon_dir / rel) != hash_or_missing(k_omega_sst_dir / rel):
            unexpected.append(rel)

    expected_present = [
        rel
        for rel in EXPECTED_MODEL_DIFFERENCES
        if (k_epsilon_dir / rel).exists() or (k_omega_sst_dir / rel).exists()
    ]
    report = {
        "common_file_hashes": common_hashes,
        "common_file_hashes_match": all(common_matches.values()),
        "mesh_hash_match": common_matches["system/blockMeshDict"],
        "velocity_bc_match": common_matches["0/U"],
        "pressure_bc_match": common_matches["0/p"],
        "physical_properties_match": common_matches["constant/physicalProperties"],
        "numerical_schemes_match": common_matches["system/fvSchemes"] and common_matches["system/fvSolution"],
        "solver_settings_match": common_matches["system/controlDict"],
        "expected_model_specific_differences": expected_present,
        "unexpected_differences": unexpected,
    }
    report["pair_audit_passed"] = bool(report["common_file_hashes_match"] and not unexpected)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k-epsilon", type=Path, required=True)
    parser.add_argument("--k-omega-sst", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("runs/rans_pitzdaily_smoke/pair_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_pair(args.k_epsilon, args.k_omega_sst, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["pair_audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
