#!/usr/bin/env python3
"""Export all public study artifacts and audit the public surface."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import export_rans_diagnostic_public as rans_exporter  # noqa: E402
import export_validation_v2_public as cavity_exporter  # noqa: E402


PUBLIC_ROOT = ROOT / "results/public"
FIGURES_ROOT = ROOT / "figures"
README = ROOT / "README.md"
AUDIT_PATH = PUBLIC_ROOT / "public_artifact_audit.json"
RAW_OPENFOAM_FIELDS = {"C", "U", "p", "k", "nut", "epsilon", "omega", "phi", "V", "yPlus", "wallShearStress"}
ABSOLUTE_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"/mnt/d/", re.IGNORECASE),
    re.compile(r"/home/"),
    re.compile(r"\\Users\\|/Users/|Users[\\/]"),
    re.compile(re.escape(Path.home().name), re.IGNORECASE),
]
README_FORBIDDEN_PHRASES = [
    "more accurate",
    "accuracy winner",
    "turbulence-model validation",
    "fully stable comparison",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def public_files(public_root: Path, figures_root: Path, audit_path: Path) -> list[Path]:
    files = [
        path
        for path in public_root.rglob("*")
        if path.is_file() and path.resolve() != audit_path.resolve() and path.suffix.lower() in {".csv", ".json"}
    ]
    files.extend(path for path in figures_root.rglob("*.png") if path.is_file())
    return sorted(files)


def file_hashes(public_root: Path, figures_root: Path, audit_path: Path) -> dict[str, dict[str, str]]:
    groups = {"csv": {}, "json": {}, "figures": {}}
    for path in public_files(public_root, figures_root, audit_path):
        rel = repo_relative(path)
        if path.suffix.lower() == ".csv":
            groups["csv"][rel] = sha256(path)
        elif path.suffix.lower() == ".json":
            groups["json"][rel] = sha256(path)
        elif path.suffix.lower() == ".png":
            groups["figures"][rel] = sha256(path)
    return groups


def absolute_path_hits(paths: list[Path], readme: Path) -> list[str]:
    hits: list[str] = []
    for path in [readme, *paths]:
        if path.suffix.lower() not in {".csv", ".json", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in ABSOLUTE_PATH_PATTERNS:
            if pattern.search(text):
                hits.append(repo_relative(path))
                break
    return sorted(set(hits))


def raw_field_hits(public_root: Path) -> list[str]:
    return sorted(repo_relative(path) for path in public_root.rglob("*") if path.is_file() and path.name in RAW_OPENFOAM_FIELDS)


def rans_registry_status(registry_path: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    rows = registry.get("snapshots", [])
    canonical = [row for row in rows if row.get("snapshot_id") == "canonical_diagnostic_snapshot"]
    historical = [row for row in rows if row.get("snapshot_id") == "historical_pre_stability_snapshot"]
    return {
        "exists": registry_path.is_file(),
        "canonical_iterations": sorted(int(row["final_iteration"]) for row in canonical),
        "historical_iterations": sorted(int(row["final_iteration"]) for row in historical),
        "canonical_intended_use": sorted(set(row.get("intended_use") for row in canonical)),
        "historical_intended_use": sorted(set(row.get("intended_use") for row in historical)),
        "canonical_statuses": sorted(set(row.get("status") for row in canonical)),
        "source_roots_relative": all(
            not any(pattern.search(str(row.get("source_root", ""))) for pattern in ABSOLUTE_PATH_PATTERNS)
            for row in rows
        ),
    }


def audit_public_artifacts(
    public_root: Path = PUBLIC_ROOT,
    figures_root: Path = FIGURES_ROOT,
    readme: Path = README,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    rans_public = public_root / "rans_pitzdaily"
    diagnostic_rows = read_csv_rows(rans_public / "diagnostic_model_summary.csv")
    diagnostic_iterations = sorted(int(row["final_iteration"]) for row in diagnostic_rows)
    manifest = read_json(rans_public / "run_manifest_public.json")
    solver_profile = read_json(rans_public / "solver_profile.json")
    registry = rans_registry_status(rans_public / "snapshot_registry.json")
    readme_text = readme.read_text(encoding="utf-8")
    files = public_files(public_root, figures_root, audit_path)
    rans_main_figures = [
        figures_root / "rans_pitzdaily/field_velocity_comparison.png",
        figures_root / "rans_pitzdaily/normalized_residual_control.png",
        figures_root / "rans_pitzdaily/sst_wall_shear_stability.png",
    ]

    checks = {
        "solver_profile_names_are_explicit": solver_profile.get("base_solver_profile") == "conservative_common"
        and solver_profile.get("canonical_snapshot_profile") == "continuation_common",
        "diagnostic_model_summary_uses_canonical_iterations": diagnostic_iterations == [1098, 1802],
        "snapshot_registry_has_canonical_and_historical_iterations": registry["canonical_iterations"] == [1098, 1802]
        and registry["historical_iterations"] == [798, 1502],
        "canonical_snapshot_status_not_rewritten_as_converged": "converged" not in registry["canonical_statuses"],
        "snapshot_registry_paths_are_relative": registry["source_roots_relative"],
        "run_manifest_is_canonical": manifest.get("snapshot_id") == "canonical_diagnostic_snapshot"
        and manifest.get("selected_profile") == "continuation_common"
        and manifest.get("intended_use") == "paired RANS model diagnostic",
        "readme_contains_quality_status": "quality_incomplete_comparison" in readme_text,
        "readme_describes_fixed_continuation": "fixed +300-iteration" in readme_text,
        "readme_references_existing_rans_main_figures": all(path.is_file() for path in rans_main_figures)
        and all(path.relative_to(ROOT).as_posix() in readme_text for path in rans_main_figures),
        "readme_avoids_forbidden_phrases": not any(phrase in readme_text for phrase in README_FORBIDDEN_PHRASES),
        "public_has_no_raw_openfoam_fields": raw_field_hits(public_root) == [],
        "public_text_has_no_absolute_paths": absolute_path_hits(files, readme) == [],
        "current_public_results_do_not_use_historical_iterations": all(
            "798" not in (rans_public / name).read_text(encoding="utf-8", errors="ignore")
            and "1502" not in (rans_public / name).read_text(encoding="utf-8", errors="ignore")
            for name in ["diagnostic_model_summary.csv", "model_summary.csv", "run_manifest_public.json"]
        ),
    }
    report = {
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "rans_snapshot_registry": registry,
        "public_file_hashes": file_hashes(public_root, figures_root, audit_path),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not report["all_checks_passed"]:
        raise RuntimeError(json.dumps(report, indent=2, sort_keys=True))
    return report


def export_all(
    readme: Path = README,
    public_root: Path = PUBLIC_ROOT,
    figures_root: Path = FIGURES_ROOT,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    cavity_exporter.export_validation_v2(
        cavity_exporter.RUN_ROOT,
        public_root / "cavity_validation_v2",
        figures_root / "cavity_validation_v2",
        readme,
    )
    rans_exporter.export_rans_diagnostic(
        rans_exporter.PRE_ROOT,
        rans_exporter.CANONICAL_ROOT,
        public_root / "rans_pitzdaily",
        figures_root / "rans_pitzdaily",
        readme,
    )
    return audit_public_artifacts(public_root, figures_root, readme, audit_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=README)
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--figures-root", type=Path, default=FIGURES_ROOT)
    parser.add_argument("--audit-path", type=Path, default=AUDIT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = export_all(args.readme, args.public_root, args.figures_root, args.audit_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
