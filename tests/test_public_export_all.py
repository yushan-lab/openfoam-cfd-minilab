from __future__ import annotations

import hashlib
import importlib.util
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


export_all = load_module(ROOT / "scripts/export_all_public.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_hash_snapshot() -> dict[str, dict[str, str] | str]:
    public_root = ROOT / "results/public"
    figures_root = ROOT / "figures"
    return {
        "README.md": sha256(ROOT / "README.md"),
        "csv": {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(public_root.rglob("*.csv"))
        },
        "json": {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(public_root.rglob("*.json"))
        },
        "figures": {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(figures_root.rglob("*.png"))
        },
    }


def write_minimal_validation_inputs(run_root: Path) -> None:
    figures = run_root / "figures"
    figures.mkdir(parents=True)
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "validation_summary.csv").write_text(
        "resolution,status,quality_gate_passed,included_in_formal_summary,final_time,requested_deltaT,"
        "observed_deltaT_min,observed_deltaT_max,observed_maxCo,steady_relative_L2_over_5,"
        "steady_gate_passed,RMSE_U,Linf_U,RMSE_V,Linf_V,sample_count_U,sample_count_V\n"
        "20,completed,True,True,30,0.0025,0.0025,0.0025,0.04,1e-6,True,0.005,0.013,0.007,0.010,17,17\n"
        "40,completed,True,True,30,0.0025,0.0025,0.0025,0.09,2e-6,True,0.002,0.003,0.004,0.007,17,17\n"
        "80,completed,True,True,30,0.0025,0.0025,0.0025,0.19,3e-6,True,0.003,0.005,0.005,0.009,17,17\n"
        "160,completed,True,True,40,0.0025,0.0025,0.0025,0.39,4e-6,True,0.004,0.006,0.006,0.010,17,17\n",
        encoding="utf-8",
    )
    (run_root / "self_convergence.csv").write_text(
        "profile,d20_40,d40_80,d80_160,p_20_40_80,p_40_80_160,differences_decrease,"
        "quality_gates_passed,self_convergence_established,observed_order_status\n"
        "u,0.004,0.001,0.0003,1.9,2.0,True,True,True,established\n"
        "v,0.006,0.002,0.0005,1.8,2.1,True,True,True,established\n",
        encoding="utf-8",
    )
    (run_root / "quality_gates.csv").write_text(
        "resolution,status,courant_gate_passed,steady_gate_passed,exact_sample_gate_passed,"
        "quality_gate_passed,material_centerline_change,quality_gate_reasons\n"
        "20,completed,True,True,True,True,1e-6,\n"
        "40,completed,True,True,True,True,2e-6,\n"
        "80,completed,True,True,True,True,3e-6,\n"
        "160,completed,True,True,True,True,4e-6,\n",
        encoding="utf-8",
    )
    (run_root / "manifest.csv").write_text(
        "resolution,status,output_root\n"
        "20,completed,/local/private/path/N20\n"
        "40,completed,/local/private/path/N40\n"
        "80,completed,/local/private/path/N80\n"
        "160,completed,/local/private/path/N160\n",
        encoding="utf-8",
    )
    for name in export_all.cavity_exporter.FIGURE_EXPORTS:
        (figures / name).write_bytes(b"png")


def write_base_readme(path: Path) -> None:
    path.write_text(
        "# Old Title\n\n"
        "Old opening.\n\n"
        "## What This Project Demonstrates\n\n"
        "Body\n\n"
        "## Smoke Reproduction Outputs\n\n"
        "Smoke body.\n\n"
        "## Governing Equations\n\n"
        "Equation body.\n\n"
        "## Physical Setup\n\n"
        "Physical body.\n\n"
        "## Mesh\n\n"
        "Mesh body.\n\n"
        "## Boundary Conditions\n\n"
        "Boundary body.\n\n"
        "## Reproduction\n\n"
        "Reproduction body.\n\n"
        "## Cloud Reproduction with GitHub Actions\n\n"
        "Cloud body.\n\n"
        "## Limitations\n\n"
        "Limitations body.\n",
        encoding="utf-8",
    )


def apply_minimal_rans_readme_update(readme: Path) -> None:
    rows = [
        {
            "model": "kEpsilon",
            "final_iteration": 1098,
            "pressure_recovery_kinematic": 5.0378,
            "lowerWall_yplus_median": 18.91305,
            "lowerWall_yplus_p95": 26.70693,
            "reattachment_length_normalized": 6.67816,
        },
        {
            "model": "kOmegaSST",
            "final_iteration": 1802,
            "pressure_recovery_kinematic": 5.441751,
            "lowerWall_yplus_median": 14.1862,
            "lowerWall_yplus_p95": 19.542955,
            "reattachment_length_normalized": 7.76367,
        },
    ]
    wall = [{"metric_scope": "overall_lowerWall", "unweighted_relative_L2": 0.051898153867788975}]
    export_all.rans_exporter.update_readme(readme, rows, wall)


def assert_public_sections_coexist(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("# OpenFOAM CFD Validation Lab: Laminar Cavity Verification and Paired RANS Diagnostics")
    assert text.count("<!-- cavity-validation-v2:start -->") == 1
    assert text.count("<!-- cavity-validation-v2:end -->") == 1
    assert text.count("<!-- rans-pitzdaily:start -->") == 1
    assert text.count("<!-- rans-pitzdaily:end -->") == 1
    assert text.count("## Validation V2 Results") == 1
    assert text.count("## RANS pitzDaily Paired Diagnostic") == 1
    assert text.count("## RANS Reproduction") == 1


def test_cavity_and_rans_readme_export_sections_coexist_in_either_order(tmp_path):
    run_root = tmp_path / "runs/cavity_validation_v2"
    write_minimal_validation_inputs(run_root)

    for order in [("cavity", "rans"), ("rans", "cavity")]:
        readme = tmp_path / f"README_{'_'.join(order)}.md"
        write_base_readme(readme)
        for exporter in order:
            if exporter == "cavity":
                export_all.cavity_exporter.export_validation_v2(
                    run_root,
                    tmp_path / "results/public/cavity_validation_v2",
                    tmp_path / "figures/cavity_validation_v2",
                    readme,
                )
            else:
                apply_minimal_rans_readme_update(readme)
        assert_public_sections_coexist(readme)


def test_export_all_public_uses_direct_imports_not_python_subprocess():
    source = (ROOT / "scripts/export_all_public.py").read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "export_validation_v2(" in source
    assert "export_rans_diagnostic(" in source


def test_public_artifact_audit_current_outputs():
    report = export_all.audit_public_artifacts()

    assert report["all_checks_passed"] is True
    assert report["checks"]["diagnostic_model_summary_uses_canonical_iterations"] is True
    assert report["checks"]["solver_profile_names_are_explicit"] is True
    assert report["checks"]["snapshot_registry_has_canonical_and_historical_iterations"] is True
    assert report["checks"]["canonical_snapshot_status_not_rewritten_as_converged"] is True
    assert report["checks"]["snapshot_registry_paths_are_relative"] is True
    assert report["checks"]["run_manifest_is_canonical"] is True
    assert report["checks"]["readme_describes_fixed_continuation"] is True
    assert report["checks"]["readme_references_existing_rans_main_figures"] is True
    assert report["checks"]["public_has_no_raw_openfoam_fields"] is True
    assert report["checks"]["public_text_has_no_absolute_paths"] is True


def test_export_all_public_is_idempotent_for_readme_public_csv_json_and_figures():
    export_all.export_all()
    first = public_hash_snapshot()

    export_all.export_all()
    second = public_hash_snapshot()

    assert second == first
