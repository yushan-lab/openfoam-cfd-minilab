from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rans_source_tree_and_manifest_are_portable():
    case_root = ROOT / "cases/rans_pitzdaily"
    source = json.loads((case_root / "case_source.json").read_text())

    assert source["openfoam_distribution"] == "Foundation"
    assert source["openfoam_version"] == "10"
    assert source["tutorial_relative_path"] == "incompressible/simpleFoam/pitzDaily"
    assert source["official_allrun_sequence"] == [
        "blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily",
        "simpleFoam",
    ]
    assert "copied_at_utc" in source
    assert source["source_file_sha256"]
    assert all(not value.startswith("/opt/") for value in source["copied_files"].values())

    for path in case_root.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="ignore")
            assert "/opt/openfoam10" not in text


def test_model_source_files_are_minimal_and_model_specific():
    case_root = ROOT / "cases/rans_pitzdaily"

    assert sorted(path.name for path in (case_root / "models/kEpsilon/0").iterdir()) == [
        "epsilon",
        "k",
        "nut",
    ]
    assert sorted(path.name for path in (case_root / "models/kOmegaSST/0").iterdir()) == [
        "k",
        "nut",
        "omega",
    ]
    assert not (case_root / "models/kEpsilon/0/omega").exists()
    assert not (case_root / "models/kOmegaSST/0/epsilon").exists()
    for forbidden in ["nuTilda", "v2", "f"]:
        assert not any(path.name == forbidden for path in case_root.rglob("*"))

    assert "model           kEpsilon;" in (
        case_root / "models/kEpsilon/constant/momentumTransport"
    ).read_text()
    assert "model           kOmegaSST;" in (
        case_root / "models/kOmegaSST/constant/momentumTransport"
    ).read_text()


def test_prepare_generates_isolated_cases_and_refuses_overwrite(tmp_path):
    prepare = load_module(ROOT / "scripts/prepare_rans_pitzdaily_case.py")
    eps_case = tmp_path / "kEpsilon"
    sst_case = tmp_path / "kOmegaSST"

    prepare.prepare_case("kEpsilon", eps_case, max_iterations=50, overwrite=False)
    prepare.prepare_case("kOmegaSST", sst_case, max_iterations=50, overwrite=False)

    assert (eps_case / "0/U").is_file()
    assert (eps_case / "0/p").is_file()
    assert (eps_case / "0/epsilon").is_file()
    assert not (eps_case / "0/omega").exists()
    assert (sst_case / "0/omega").is_file()
    assert not (sst_case / "0/epsilon").exists()
    assert "endTime         50;" in (eps_case / "system/controlDict").read_text()
    assert "endTime         50;" in (sst_case / "system/controlDict").read_text()
    assert (eps_case / "case_manifest.json").is_file()
    assert (sst_case / "case_manifest.json").is_file()

    try:
        prepare.prepare_case("kEpsilon", eps_case, max_iterations=50, overwrite=False)
    except FileExistsError as exc:
        assert "Refusing to overwrite" in str(exc)
    else:
        raise AssertionError("prepare_case should refuse a non-empty output directory")


def test_pair_audit_accepts_expected_model_differences_and_detects_unexpected(tmp_path):
    prepare = load_module(ROOT / "scripts/prepare_rans_pitzdaily_case.py")
    audit = load_module(ROOT / "scripts/audit_rans_case_pair.py")
    eps_case = tmp_path / "kEpsilon"
    sst_case = tmp_path / "kOmegaSST"
    prepare.prepare_case("kEpsilon", eps_case, max_iterations=50, overwrite=False)
    prepare.prepare_case("kOmegaSST", sst_case, max_iterations=50, overwrite=False)

    report = audit.audit_pair(eps_case, sst_case, tmp_path / "pair_audit.json")

    assert report["pair_audit_passed"]
    assert report["mesh_hash_match"]
    assert report["velocity_bc_match"]
    assert report["pressure_bc_match"]
    assert report["physical_properties_match"]
    assert report["numerical_schemes_match"]
    assert report["solver_settings_match"]
    assert report["unexpected_differences"] == []
    assert "0/epsilon" in report["expected_model_specific_differences"]
    assert "0/omega" in report["expected_model_specific_differences"]

    (sst_case / "0/U").write_text((sst_case / "0/U").read_text() + "\n// drift\n")
    report = audit.audit_pair(eps_case, sst_case, tmp_path / "pair_audit_failed.json")

    assert not report["pair_audit_passed"]
    assert "0/U" in report["unexpected_differences"]


def test_simplefoam_parser_supports_epsilon_omega_and_failures(tmp_path):
    parser = load_module(ROOT / "scripts/parse_simplefoam_log.py")
    log = tmp_path / "simpleFoam.log"
    log.write_text(
        """
Time = 1
smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-05, No Iterations 2
smoothSolver:  Solving for Uy, Initial residual = 0.2, Final residual = 2e-05, No Iterations 3
GAMG:  Solving for p, Initial residual = 0.3, Final residual = 3e-05, No Iterations 4
smoothSolver:  Solving for k, Initial residual = 0.4, Final residual = 4e-05, No Iterations 5
smoothSolver:  Solving for epsilon, Initial residual = 0.5, Final residual = 5e-05, No Iterations 6
time step continuity errors : sum local = 1e-08, global = 2e-09, cumulative = 3e-09
Time = 2
smoothSolver:  Solving for omega, Initial residual = 0.6, Final residual = 6e-05, No Iterations 7
SIMPLE solution converged in 2 iterations
End
"""
    )
    result_dir = tmp_path / "results"

    summary = parser.parse_and_write(log, result_dir)

    residual_rows = list(csv.DictReader((result_dir / "residuals.csv").open()))
    assert {row["field"] for row in residual_rows} >= {"Ux", "Uy", "p", "k", "epsilon", "omega"}
    assert summary["actual_iterations"] == 2
    assert summary["converged"] is True
    assert summary["ended"] is True
    assert summary["has_nan"] is False
    assert summary["has_fatal_error"] is False
    assert summary["final_residuals"]["omega"]["linear_iterations"] == 7

    failed = parser.parse_solver_log("Time = 1\nFOAM FATAL ERROR: Unknown turbulence model\n")
    assert failed["has_fatal_error"]
    assert failed["has_unknown_model"]


def test_run_script_contains_required_safety_contracts():
    script = (ROOT / "scripts/run_rans_pitzdaily_case.sh").read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in script
    for cmd in ["blockMesh", "checkMesh", "potentialFoam", "simpleFoam", "postProcess"]:
        assert f"command -v {cmd}" in script
    assert "prepare_rans_pitzdaily_case.py" in script
    assert "audit_rans_case_pair.py" in script
    assert "parse_simplefoam_log.py" in script
    assert "blockMesh -case" in script
    assert "checkMesh -case" in script
    assert "simpleFoam -case" in script
    assert "Mesh OK" in script
    assert "floating point exception" in script
    assert "unknown turbulence model" in script
    assert "missing field" in script
    assert "run_metadata.json" in script
