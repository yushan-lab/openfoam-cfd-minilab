from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


formal = load_module(ROOT / "scripts/rans_pitzdaily_formal_tools.py")
fields = load_module(ROOT / "scripts/plot_rans_pitzdaily_fields.py")


def minimal_model_data(centres_sst: np.ndarray | None = None) -> dict[str, dict]:
    centres = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    centres_sst = centres if centres_sst is None else centres_sst
    scalar = np.array([1.0, 2.0])
    vector = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    return {
        "kEpsilon": {
            "manifest": {"mesh_hash": "mesh", "profile_name": "conservative_common"},
            "summary": {"status": "converged", "quality_gate_status": "passed"},
            "fields": {
                "C": {"values": centres, "dimensions": "[0 1 0 0 0 0 0]"},
                "U": {"values": vector, "dimensions": "[0 1 -1 0 0 0 0]"},
                "p": {"values": scalar, "dimensions": "[0 2 -2 0 0 0 0]"},
                "k": {"values": scalar, "dimensions": "[0 2 -2 0 0 0 0]"},
                "nut": {"values": scalar, "dimensions": "[0 2 -1 0 0 0 0]"},
                "epsilon": {"values": scalar, "dimensions": "[0 2 -3 0 0 0 0]"},
            },
        },
        "kOmegaSST": {
            "manifest": {"mesh_hash": "mesh", "profile_name": "conservative_common"},
            "summary": {"status": "converged", "quality_gate_status": "passed"},
            "fields": {
                "C": {"values": centres_sst, "dimensions": "[0 1 0 0 0 0 0]"},
                "U": {"values": vector, "dimensions": "[0 1 -1 0 0 0 0]"},
                "p": {"values": scalar, "dimensions": "[0 2 -2 0 0 0 0]"},
                "k": {"values": scalar, "dimensions": "[0 2 -2 0 0 0 0]"},
                "nut": {"values": scalar, "dimensions": "[0 2 -1 0 0 0 0]"},
                "omega": {"values": scalar, "dimensions": "[0 0 -1 0 0 0 0]"},
            },
        },
    }


def test_fixed_model_color_mapping_and_residual_field_colors():
    assert formal.MODEL_COLORS == {"kEpsilon": "tab:blue", "kOmegaSST": "tab:orange"}
    assert fields.MODEL_COLORS == formal.MODEL_COLORS
    assert formal.residual_color_for_model_field("kEpsilon", "epsilon") == "tab:blue"
    assert formal.residual_color_for_model_field("kOmegaSST", "omega") == "tab:orange"
    assert formal.residual_color_for_model_field("kEpsilon", "Ux") == "tab:blue"
    assert formal.residual_color_for_model_field("kOmegaSST", "Ux") == "tab:orange"


def test_cell_center_consistency_audit_passes_for_matching_centres():
    audit = fields.field_visualization_audit(minimal_model_data(), "conservative_common")

    assert audit["cell_count_consistent"] is True
    assert audit["coordinates_identical"] is True
    assert audit["mesh_hash_consistent"] is True
    assert audit["difference_map_allowed"] is True
    assert audit["all_required_checks_passed"] is True


def test_mismatched_cell_centres_refuse_difference_map():
    shifted = np.array([[0.0, 0.0, 0.0], [1.1, 0.0, 0.0]])
    audit = fields.field_visualization_audit(minimal_model_data(shifted), "conservative_common")

    assert audit["cell_count_consistent"] is True
    assert audit["coordinates_identical"] is False
    assert audit["difference_map_allowed"] is False


def test_continuation_profile_can_come_from_solver_summary():
    data = minimal_model_data()
    for model_data in data.values():
        model_data["manifest"]["profile_name"] = "conservative_common"
        model_data["summary"]["profile_name"] = "continuation_common"

    audit = fields.field_visualization_audit(data, "continuation_common")

    assert audit["profile_ok"] is True


def test_shared_and_symmetric_color_limits():
    assert fields.symmetric_limits(np.array([-2.0, 3.0]), np.array([1.0])) == (-3.0, 3.0)
    assert fields.shared_limits(np.array([2.0, 4.0]), np.array([1.0, 3.0]), include_zero=True) == (0.0, 4.0)
    assert fields.symmetric_limits(np.array([-0.25, 0.1])) == (-0.25, 0.25)
    assert fields.nut_ratio_limits(np.array([2.0, 4.0]), np.array([1.0, 3.0])) == (0.0, 4.0)


def test_normalized_delta_p_subtracts_each_outlet_mean():
    eps_p = np.array([10.0, 11.0])
    sst_p = np.array([30.0, 34.0])
    delta = fields.normalized_pressure_difference(eps_p, 9.0, sst_p, 29.0)

    assert np.allclose(delta, np.array([0.0, 3.0]))


def test_pressure_label_has_kinematic_units_and_no_pa():
    label = fields.pressure_label()

    assert "Pa" not in label
    assert "m^2/s^2" in label


def test_reattachment_marker_comes_from_summary_value():
    assert fields.reattachment_marker_from_summary({"x_reattachment_raw": "0.203"}) == 0.203


def test_public_manifest_rejects_absolute_paths_and_raw_field_names():
    assert fields.public_manifest_has_no_absolute_paths({"models": [{"model": "kEpsilon"}]}) is True
    assert fields.public_manifest_has_no_absolute_paths({"path": "D:\\secret\\case\\U"}) is False
    assert fields.public_manifest_has_no_absolute_paths({"path": "/mnt/d/case/U"}) is False
    assert fields.is_raw_field_public_path(Path("results/public/rans_pitzdaily/U")) is True
    assert fields.is_raw_field_public_path(Path("results/public/rans_pitzdaily/model_summary.csv")) is False


def test_readme_snippet_rounds_values_and_marks_failed_stability(tmp_path):
    final_audit = tmp_path / "final_audit"
    final_audit.mkdir(parents=True)
    (final_audit / "selected_model_summary.csv").write_text(
        "model,actual_iterations,pressure_recovery_kinematic,lowerWall_yplus_median,lowerWall_yplus_p95,reattachment_length_normalized\n"
        "kEpsilon,798.0,4.588655,19.16405,27.59422,6.420447808756585\n"
        "kOmegaSST,1502.0,5.815155,14.3668,19.59597,7.884175344661831\n",
        encoding="utf-8",
    )
    (final_audit / "qoi_stability.json").write_text('{"qoi_stability_passed": false}\n', encoding="utf-8")

    fields.write_readme_snippet(tmp_path)
    snippet = (final_audit / "readme_rans_section.txt").read_text(encoding="utf-8")

    assert "kEpsilon, 798, 4.589, 19.16, 27.59, 6.42" in snippet
    assert "kOmegaSST, 1502, 5.815, 14.37, 19.60, 7.88" in snippet
    assert "Post-convergence stability:\n- not passed" in snippet
    assert "quality_incomplete_comparison" in snippet
