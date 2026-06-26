from __future__ import annotations

import csv
import importlib.util
import json
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


diagnostic = load_module(ROOT / "scripts/export_rans_diagnostic_public.py")


def write_minimal_audit(root: Path, iterations: dict[str, int], qoi: dict[str, bool]) -> None:
    final_audit = root / "final_audit"
    final_audit.mkdir(parents=True)
    (final_audit / "selected_model_summary.csv").write_text(
        "model,actual_iterations,status,quality_gate_status\n"
        + "\n".join(
            f"{model},{iteration},max_iterations_reached,incomplete_convergence"
            for model, iteration in iterations.items()
        )
        + "\n",
        encoding="utf-8",
    )
    (final_audit / "qoi_stability.csv").write_text(
        "model,final_iteration,qoi_stability_passed,wall_shear_curve_relative_L2\n"
        + "\n".join(
            f"{model},{iterations[model]},{qoi[model]},0.05"
            for model in iterations
        )
        + "\n",
        encoding="utf-8",
    )


def test_snapshot_registry_separates_pre_and_post_continuation(tmp_path):
    pre = tmp_path / "pre"
    canonical = tmp_path / "stability_continuation"
    write_minimal_audit(pre, {"kEpsilon": 798, "kOmegaSST": 1502}, {"kEpsilon": False, "kOmegaSST": False})
    write_minimal_audit(canonical, {"kEpsilon": 1098, "kOmegaSST": 1802}, {"kEpsilon": True, "kOmegaSST": False})

    registry = diagnostic.snapshot_registry(pre, canonical)
    rows = registry["snapshots"]
    canonical_rows = [row for row in rows if row["snapshot_id"] == "canonical_diagnostic_snapshot"]
    historical_rows = [row for row in rows if row["snapshot_id"] == "historical_pre_stability_snapshot"]

    assert {row["final_iteration"] for row in canonical_rows} == {1098, 1802}
    assert {row["final_iteration"] for row in historical_rows} == {798, 1502}
    assert all(row["intended_use"] == "canonical_diagnostic_snapshot" for row in canonical_rows)
    assert all(row["intended_use"] == "historical_pre_stability_snapshot" for row in historical_rows)
    assert all(row["source_root"].endswith("stability_continuation/continuation_common") for row in canonical_rows)
    assert all(row["source_root"].endswith("conservative_common") for row in historical_rows)


def test_coordinate_mismatch_refuses_direct_array_l2():
    previous = [
        {"face_index": 0, "x": 0.0, "y": 0.0, "z": 0.0, "tau_downstream_tangent": 1.0},
        {"face_index": 1, "x": 1.0, "y": 0.0, "z": 0.0, "tau_downstream_tangent": 1.0},
    ]
    final = [
        {"face_index": 0, "x": 0.0, "y": 0.0, "z": 0.0, "tau_downstream_tangent": 1.0},
        {"face_index": 1, "x": 1.1, "y": 0.0, "z": 0.0, "tau_downstream_tangent": 1.0},
    ]

    audit = diagnostic.coordinate_audit(previous, final)

    assert audit["direct_index_l2_allowed"] is False
    assert audit["fallback_method"] == "common_x_interpolation"
    try:
        diagnostic.aligned_wall_shear(previous, final, audit)
    except ValueError as exc:
        assert "Coordinate mismatch" in str(exc)
    else:
        raise AssertionError("Expected coordinate mismatch to reject direct face-index comparison")


def test_wall_shear_gate_keeps_unweighted_l2_as_registered_metric():
    previous = [1.0, 0.0]
    final = [1.0, 0.06]
    unweighted = diagnostic.relative_l2(previous, final)
    weighted = diagnostic.relative_l2(previous, final, [10.0, 1.0])

    assert unweighted > diagnostic.WALL_SHEAR_THRESHOLD
    assert weighted < unweighted
    assert (unweighted <= diagnostic.WALL_SHEAR_THRESHOLD) is False


def test_readme_section_is_diagnostic_and_not_accuracy_winner(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n\n## Smoke Reproduction Outputs\n\nbody\n", encoding="utf-8")
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

    diagnostic.update_readme(readme, rows, wall)
    text = readme.read_text(encoding="utf-8")

    assert "paired RANS model diagnostic" in text
    assert "quality_incomplete_comparison" in text
    assert "fixed +300-iteration QoI stability snapshots" in text
    assert "post-convergence QoI stability audit" in text
    assert "solver profile and canonical snapshot profile are separate concepts" in text
    assert "1098" in text and "1802" in text
    assert "798" not in text and "1502" not in text
    assert "![RANS velocity field comparison](figures/rans_pitzdaily/field_velocity_comparison.png)" in text
    assert "![RANS normalized residual-control history](figures/rans_pitzdaily/normalized_residual_control.png)" in text
    assert "![kOmegaSST lower-wall shear stability](figures/rans_pitzdaily/sst_wall_shear_stability.png)" in text
    assert "more accurate" not in text
    assert "accuracy winner" not in text
    assert "turbulence-model validation" not in text


def test_current_public_summary_uses_only_continuation_snapshots():
    path = ROOT / "results/public/rans_pitzdaily/diagnostic_model_summary.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert {int(row["final_iteration"]) for row in rows} == {1098, 1802}
    assert {row["snapshot_id"] for row in rows} == {"canonical_diagnostic_snapshot"}
    assert "798" not in path.read_text(encoding="utf-8")
    assert "1502" not in path.read_text(encoding="utf-8")


def test_public_snapshot_registry_exists_and_separates_snapshot_uses():
    path = ROOT / "results/public/rans_pitzdaily/snapshot_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    rows = registry["snapshots"]
    canonical = [row for row in rows if row["snapshot_id"] == "canonical_diagnostic_snapshot"]
    historical = [row for row in rows if row["snapshot_id"] == "historical_pre_stability_snapshot"]

    assert path.is_file()
    assert {row["final_iteration"] for row in canonical} == {1098, 1802}
    assert {row["final_iteration"] for row in historical} == {798, 1502}
    assert {row["intended_use"] for row in canonical} == {"canonical_diagnostic_snapshot"}
    assert {row["intended_use"] for row in historical} == {"historical_pre_stability_snapshot"}
    assert all(not Path(row["source_root"]).is_absolute() for row in rows)
    assert "D:" not in path.read_text(encoding="utf-8")
    assert "C:" not in path.read_text(encoding="utf-8")
    assert "/mnt/d/" not in path.read_text(encoding="utf-8")
    assert "/home/" not in path.read_text(encoding="utf-8")


def test_public_solver_profile_records_common_relaxation_and_continuation():
    profile = json.loads((ROOT / "results/public/rans_pitzdaily/solver_profile.json").read_text(encoding="utf-8"))

    assert profile == {
        "profile": "conservative_common",
        "base_solver_profile": "conservative_common",
        "canonical_snapshot_profile": "continuation_common",
        "U_exact_entry": 0.7,
        "equation_catch_all_regex": ".*",
        "equation_catch_all_value": 0.5,
        "continuation_additional_iterations": 300,
        "continuation_residual_control_disabled": True,
        "applied_to_models": ["kEpsilon", "kOmegaSST"],
        "openfoam_version": "10",
    }


def test_current_wall_shear_coordinate_audit_confirms_direct_correspondence():
    audit_path = ROOT / "runs/rans_pitzdaily_formal_v2/stability_continuation/final_audit/sst_wall_shear_coordinate_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))

    assert audit["face_count_match"] is True
    assert audit["face_index_match"] is True
    assert audit["coordinate_match"] is True
    assert audit["direct_index_l2_allowed"] is True

    csv_path = audit_path.with_suffix(".csv")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert {
            "face_index",
            "x_previous",
            "x_final",
            "face_area",
            "wall_face_length",
            "tau_downstream_tangent_previous",
            "tau_downstream_tangent_final",
        }.issubset(reader.fieldnames or [])


def test_public_results_do_not_contain_raw_openfoam_fields():
    public_root = ROOT / "results/public/rans_pitzdaily"
    raw_field_names = {"C", "U", "p", "k", "nut", "epsilon", "omega", "phi", "V", "yPlus", "wallShearStress"}
    leaked = [path for path in public_root.rglob("*") if path.is_file() and path.name in raw_field_names]

    assert leaked == []
