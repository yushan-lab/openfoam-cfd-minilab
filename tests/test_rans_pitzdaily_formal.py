from __future__ import annotations

import csv
import importlib.util
import json
import math
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


formal = load_module(ROOT / "scripts/rans_pitzdaily_formal_tools.py")
prepare = load_module(ROOT / "scripts/prepare_rans_pitzdaily_case.py")
pair_audit = load_module(ROOT / "scripts/audit_rans_case_pair.py")


def test_inlet_turbulence_intensity_and_length_scale_formulas():
    assert math.isclose(formal.turbulence_intensity(0.375, 10.0), 0.05)

    l_epsilon = formal.epsilon_length_scale(k=0.375, epsilon=14.855)
    l_omega = formal.omega_length_scale(k=0.375, omega=440.15)

    assert math.isclose(l_epsilon, 0.0025394, rel_tol=2e-3)
    assert math.isclose(l_omega, 0.0025394, rel_tol=2e-3)
    assert formal.relative_difference(l_epsilon, l_omega) < 0.01


def test_length_scale_audit_reports_mismatch_without_rewriting_inputs(tmp_path):
    eps_case = tmp_path / "kEpsilon"
    sst_case = tmp_path / "kOmegaSST"
    prepare.prepare_case("kEpsilon", eps_case, max_iterations=50)
    prepare.prepare_case("kOmegaSST", sst_case, max_iterations=50)

    report, rows = formal.audit_turbulence_initialization(eps_case, sst_case)

    assert report["inlet_U_match"] is True
    assert report["inlet_k_match"] is True
    assert report["epsilon_positive"] is True
    assert report["omega_positive"] is True
    assert report["length_scale_relative_difference"] < 0.01
    assert report["length_scale_gate_passed"] is True
    assert report["auto_corrected_inputs"] is False
    assert {row["model"] for row in rows} == {"kEpsilon", "kOmegaSST"}

    omega_file = sst_case / "0/omega"
    omega_file.write_text(omega_file.read_text().replace("440.15", "100.0"))
    report, _ = formal.audit_turbulence_initialization(eps_case, sst_case)

    assert report["length_scale_relative_difference"] > 0.2
    assert report["length_scale_gate_passed"] is False


def test_residual_control_covers_epsilon_and_omega_with_same_threshold():
    fv_solution = (ROOT / "cases/rans_pitzdaily/base/system/fvSolution").read_text()

    thresholds = formal.residual_control_thresholds(fv_solution, ["U", "p", "k", "epsilon", "omega"])

    assert thresholds["U"] == 1e-3
    assert thresholds["p"] == 1e-2
    assert thresholds["k"] == 1e-3
    assert thresholds["epsilon"] == 1e-3
    assert thresholds["omega"] == 1e-3


def test_profile_relaxation_does_not_change_residual_control(tmp_path):
    case = tmp_path / "kEpsilon"
    prepare.prepare_case(
        "kEpsilon",
        case,
        max_iterations=100,
        overwrite=True,
        profile_name="conservative_common",
        u_relaxation=0.7,
        equation_catch_all_relaxation=0.5,
    )

    fv_solution = (case / "system/fvSolution").read_text()
    thresholds = formal.residual_control_thresholds(fv_solution, ["U", "p", "k", "epsilon"])

    assert thresholds["U"] == 1e-3
    assert thresholds["p"] == 1e-2
    assert thresholds["k"] == 1e-3
    assert "        U               0.7;" in fv_solution
    assert '        ".*"            0.5;' in fv_solution


def test_postprocess_capability_audit_schema():
    capabilities = [
        formal.PostprocessCapability(
            name="yPlus",
            template_path="/opt/openfoam10/etc/caseDicts/postProcessing/fields/yPlus",
            command="postProcess -case CASE -func yPlus -latestTime",
            output_location="CASE/<time>/yPlus",
            dimensions="[0 0 0 0 0 0 0]",
            available=True,
            smoke_status="not_run",
        )
    ]

    as_dict = formal.postprocess_capability_report(capabilities)

    assert as_dict["capabilities"][0]["name"] == "yPlus"
    assert set(as_dict["capabilities"][0]) == {
        "name",
        "template_path",
        "command",
        "output_location",
        "dimensions",
        "available",
        "smoke_status",
    }


def test_postprocess_capability_audit_marks_supplied_case_outputs(tmp_path):
    case = tmp_path / "case"
    time_dir = case / "10"
    time_dir.mkdir(parents=True)
    for name, dimensions in {
        "yPlus": "[0 0 0 0 0 0 0]",
        "wallShearStress": "[0 2 -2 0 0 0 0]",
        "C": "[0 1 0 0 0 0 0]",
        "V": "[0 3 0 0 0 0 0]",
        "phi": "[0 3 -1 0 0 0 0]",
        "p": "[0 2 -2 0 0 0 0]",
    }.items():
        (time_dir / name).write_text(f"dimensions      {dimensions};\n")
    for function_name in [
        "patchFlowRate(phi,patch=inlet)",
        "patchAverage(p,patch=inlet)",
    ]:
        out = case / "postProcessing" / function_name / "10" / "surfaceFieldValue.dat"
        out.parent.mkdir(parents=True)
        out.write_text("# Time value\n10 1\n")

    report = formal.discover_postprocess_capabilities(case)
    by_name = {row["name"]: row for row in report["capabilities"]}

    assert by_name["yPlus"]["smoke_status"] == "succeeded_on_supplied_case"
    assert by_name["wallShearStress"]["dimensions"] == "[0 2 -2 0 0 0 0]"
    assert by_name["patchFlowRate"]["smoke_status"] == "succeeded_on_supplied_case"
    assert by_name["patchFlowRate"]["dimensions"] == "[0 3 -1 0 0 0 0]"
    assert by_name["patchAverage"]["dimensions"] == "[0 2 -2 0 0 0 0]"


def test_signed_flow_balance_and_volume_units_are_not_mass_units():
    rows = [
        {"patch": "inlet", "signed_volumetric_flow_rate": -0.20},
        {"patch": "outlet", "signed_volumetric_flow_rate": 0.198},
    ]

    assert math.isclose(formal.relative_flow_imbalance(rows), 0.01)
    assert formal.flow_rate_quantity_label("[0 3 -1 0 0 0 0]") == "volumetric flow rate"
    assert "kg/s" not in formal.flow_rate_quantity_label("[0 3 -1 0 0 0 0]")


def test_pressure_and_wall_shear_labels_preserve_dimensions_without_pa():
    assert formal.pressure_quantity_label("[0 2 -2 0 0 0 0]") == "kinematic pressure"
    assert "Pa" not in formal.pressure_quantity_label("[0 2 -2 0 0 0 0]")
    assert formal.pressure_recovery_kinematic(p_in=-5.0, p_out=1.0) == 6.0

    row = formal.wall_shear_row(
        model="kEpsilon",
        patch="lowerWall",
        face_index=4,
        centre=(0.1, -0.02, 0.0),
        tau=(2.0, 0.5, 0.0),
        inlet_u=(10.0, 0.0, 0.0),
        dimensions="[0 2 -2 0 0 0 0]",
    )

    assert row["tau_streamwise"] == 2.0
    assert row["field_dimensions"] == "[0 2 -2 0 0 0 0]"


def test_yplus_summary_includes_percentiles_and_finite_fraction():
    summary = formal.summarize_values([1.0, 2.0, 3.0, 4.0, 100.0, float("nan")])

    assert summary["count"] == 5
    assert summary["min"] == 1.0
    assert summary["median"] == 3.0
    assert summary["p05"] > 1.0
    assert summary["p95"] < 100.0
    assert math.isclose(summary["finite_fraction"], 5 / 6)


def test_streamwise_shear_projection_uses_inlet_direction():
    assert math.isclose(
        formal.streamwise_projection((3.0, 4.0, 0.0), (0.0, 10.0, 0.0)),
        4.0,
    )


def test_reattachment_calibrates_attached_sign_for_positive_to_negative():
    upstream = [{"x": -0.02 + i * 0.001, "y": 0.0, "tau_downstream_tangent": -0.4} for i in range(15)]
    downstream = [
        {"x": 0.001 + i * 0.001, "y": -0.025, "tau_downstream_tangent": 0.2}
        for i in range(12)
    ] + [
        {"x": 0.013 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.3}
        for i in range(12)
    ]

    detected = formal.detect_reattachment(upstream + downstream, {"step_x": 0.0, "step_height": 0.0254})

    assert detected["status"] == "detected"
    assert detected["attached_sign"] == -1
    assert detected["selected_crossing"]["direction"] == "positive-to-negative"
    assert 0.011 < detected["x_reattachment_raw"] < 0.014


def test_reattachment_calibrates_attached_sign_for_negative_to_positive():
    upstream = [{"x": -0.02 + i * 0.001, "y": 0.0, "tau_downstream_tangent": 0.4} for i in range(15)]
    downstream = [
        {"x": 0.001 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.2}
        for i in range(12)
    ] + [
        {"x": 0.013 + i * 0.001, "y": -0.025, "tau_downstream_tangent": 0.3}
        for i in range(12)
    ]

    detected = formal.detect_reattachment(upstream + downstream, {"step_x": 0.0, "step_height": 0.0254})

    assert detected["status"] == "detected"
    assert detected["attached_sign"] == 1
    assert detected["selected_crossing"]["direction"] == "negative-to-positive"


def test_near_step_oscillation_is_rejected_without_sustained_separation():
    upstream = [{"x": -0.02 + i * 0.001, "y": 0.0, "tau_downstream_tangent": -0.4} for i in range(15)]
    downstream = [
        {"x": 0.001, "y": -0.025, "tau_downstream_tangent": 0.1},
        {"x": 0.002, "y": -0.025, "tau_downstream_tangent": -0.1},
        {"x": 0.003, "y": -0.025, "tau_downstream_tangent": 0.1},
    ] + [
        {"x": 0.004 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.2}
        for i in range(12)
    ]

    result = formal.detect_reattachment(upstream + downstream, {"step_x": 0.0, "step_height": 0.0254})

    assert result["status"] == "not_detected"
    assert result["confidence_status"] == "no_stable_recovery"


def test_multiple_crossings_select_final_stable_recovery():
    upstream = [{"x": -0.02 + i * 0.001, "y": 0.0, "tau_downstream_tangent": -0.4} for i in range(15)]
    first_sep = [{"x": 0.001 + i * 0.001, "y": -0.025, "tau_downstream_tangent": 0.2} for i in range(12)]
    first_attach = [{"x": 0.013 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.2} for i in range(12)]
    later_sep = [{"x": 0.025 + i * 0.001, "y": -0.025, "tau_downstream_tangent": 0.2} for i in range(12)]
    final_attach = [{"x": 0.037 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.2} for i in range(12)]

    result = formal.detect_reattachment(
        upstream + first_sep + first_attach + later_sep + final_attach,
        {"step_x": 0.0, "step_height": 0.0254},
    )

    assert result["status"] == "detected"
    assert len(result["all_crossings"]) == 2
    assert result["selected_crossing"]["crossing_index"] == 1
    assert result["x_reattachment_raw"] > 0.035


def test_no_sustained_recovery_returns_not_detected():
    upstream = [{"x": -0.02 + i * 0.001, "y": 0.0, "tau_downstream_tangent": -0.4} for i in range(15)]
    downstream = [
        {"x": 0.001 + i * 0.001, "y": -0.025, "tau_downstream_tangent": 0.2}
        for i in range(12)
    ] + [
        {"x": 0.013 + i * 0.001, "y": -0.025, "tau_downstream_tangent": -0.3}
        for i in range(5)
    ]

    result = formal.detect_reattachment(upstream + downstream, {"step_x": 0.0, "step_height": 0.0254})

    assert result["status"] == "not_detected"


def test_downstream_lower_wall_filter_excludes_step_vertical_faces():
    rows = [
        {"x": -0.1, "y": 0.0, "tau_streamwise": -1.0},
        {"x": 0.0, "y": -0.01, "tau_streamwise": 1.0},
        {"x": 0.01, "y": -0.025, "tau_streamwise": -1.0},
        {"x": 0.02, "y": -0.025, "tau_streamwise": 0.5},
        {"x": 0.03, "y": -0.025, "tau_streamwise": 0.6},
        {"x": 0.04, "y": -0.025, "tau_streamwise": 0.7},
    ]

    downstream = formal.downstream_lower_wall_rows(rows, {"step_x": 0.0})

    assert [row["x"] for row in downstream] == [0.01, 0.02, 0.03, 0.04]


def test_geometry_audit_does_not_invent_step_height():
    report = formal.audit_geometry_from_block_mesh_text(
        """
vertices
(
    (0 0 0)
    (1 0 0)
);
boundary
(
);
"""
    )

    assert report["step_height_status"] == "not_detected"
    assert report["step_height"] is None


def test_openfoam_mesh_lists_and_patch_vector_fields_parse_with_footers(tmp_path):
    points = tmp_path / "points"
    points.write_text(
        """
4
(
(0 0 0)
(1 0 0)
(1 1 0)
(0 1 0)
)

// ************************************************************************* //
"""
    )
    faces = tmp_path / "faces"
    faces.write_text(
        """
1
(
4(0 1 2 3)
)

// ************************************************************************* //
"""
    )
    field = tmp_path / "wallShearStress"
    field.write_text(
        """
dimensions      [0 2 -2 0 0 0 0];
boundaryField
{
    lowerWall
    {
        type            calculated;
        value           nonuniform List<vector>
2
(
(-1 0 0)
(2 0 0)
)
;
    }
}
"""
    )

    assert formal.read_openfoam_list(points, "vector") == [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    assert formal.read_openfoam_list(faces, "face") == [[0, 1, 2, 3]]
    dimensions, values = formal.read_patch_field_values(field, "lowerWall")
    assert dimensions == "[0 2 -2 0 0 0 0]"
    assert values == [(-1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]


def test_solver_status_and_failed_manifest_rows_are_retained(tmp_path):
    converged = formal.classify_solver_status(
        {"ended": True, "converged": True, "failed": False},
        solver_exit_code=0,
    )
    incomplete = formal.classify_solver_status(
        {"ended": True, "converged": False, "failed": False},
        solver_exit_code=0,
    )
    failed = formal.classify_solver_status(
        {"ended": False, "converged": False, "failed": True},
        solver_exit_code=1,
    )

    assert converged == "converged"
    assert incomplete == "max_iterations_reached"
    assert failed == "failed"

    rows = [
        formal.manifest_row("kEpsilon", "failed", 12, tmp_path / "kEpsilon", 1.0, "abc", "10", True),
        formal.manifest_row("kOmegaSST", "converged", 10, tmp_path / "kOmegaSST", 1.0, "abc", "10", True),
    ]
    out = tmp_path / "manifest.csv"
    formal.write_manifest(out, rows)

    written = list(csv.DictReader(out.open()))
    assert [row["model"] for row in written] == ["kEpsilon", "kOmegaSST"]
    assert written[0]["status"] == "failed"


def test_initial_residual_thresholds_determine_residual_control_not_final_residual():
    rows = [
        {"iteration": 10, "field": "Ux", "initial_residual": 9e-4, "final_residual": 1.0},
        {"iteration": 10, "field": "Uy", "initial_residual": 8e-4, "final_residual": 1.0},
        {"iteration": 10, "field": "p", "initial_residual": 9e-3, "final_residual": 1.0},
        {"iteration": 10, "field": "k", "initial_residual": 9e-4, "final_residual": 1.0},
        {"iteration": 10, "field": "omega", "initial_residual": 9e-4, "final_residual": 1.0},
    ]

    report = formal.residual_control_report(rows)

    assert report["max_initial_residual_at_final_iteration"] == 9e-3
    assert report["residual_control_passed"] is True
    assert report["max_linear_solver_final_residual"] == 1.0


def test_incomplete_results_are_marked_provisional():
    assert formal.result_status_for_model("converged") == "complete_converged"
    assert formal.result_status_for_model("max_iterations_reached") == "provisional_incomplete_convergence"
    assert formal.model_display_name("kOmegaSST", "max_iterations_reached") == "kOmegaSST (not converged)"


def test_official_profile_does_not_run_potentialfoam_and_fallback_is_common():
    official = formal.solver_profile("official_common")
    fallback = formal.solver_profile("conservative_common")

    assert official["commands"] == ["blockMesh", "simpleFoam"]
    assert "potentialFoam" not in official["commands"]
    assert official["max_iterations"] == 4000
    assert fallback["max_iterations"] == 8000
    assert fallback["applies_to_models"] == ["kEpsilon", "kOmegaSST"]
    assert fallback["relaxation"]["U_exact_entry"] == 0.7
    assert fallback["relaxation"]["equation_catch_all_regex"] == ".*"
    assert fallback["relaxation"]["equation_catch_all_value"] == 0.5


def test_field_relative_l2_change_for_scalar_and_vector_lists(tmp_path):
    final = tmp_path / "U_final"
    previous = tmp_path / "U_previous"
    final.write_text(
        """
internalField   nonuniform List<vector>
2
(
(2 0 0)
(0 2 0)
)
;
"""
    )
    previous.write_text(
        """
internalField   nonuniform List<vector>
2
(
(1 0 0)
(0 1 0)
)
;
"""
    )

    report = formal.field_relative_l2_change(final, previous)

    assert report["available"] is True
    assert math.isclose(report["relative_l2"], 0.5)


def test_field_stability_uses_nearest_saved_time_and_reports_actual_offset(tmp_path):
    case = tmp_path / "case"
    for time_name, value in [("0", 0), ("300", 1), ("700", 2), ("798", 3)]:
        time_dir = case / time_name
        time_dir.mkdir(parents=True)
        for field in ["U", "p", "k", "nut", "epsilon"]:
            time_dir.joinpath(field).write_text(
                f"""
internalField   nonuniform List<scalar>
1
(
{value}
)
;
"""
            )

    rows = formal.field_stability_rows(case, "kEpsilon", "798")
    by_key = {(row["field"], row["requested_offset_iterations"]): row for row in rows}

    assert by_key[("U", 100)]["comparison_time"] == "700"
    assert by_key[("U", 100)]["target_iteration"] == 698
    assert by_key[("U", 100)]["actual_offset_iterations"] == 98
    assert by_key[("U", 500)]["comparison_time"] == "300"
    assert by_key[("U", 500)]["target_iteration"] == 298
    assert by_key[("U", 500)]["actual_offset_iterations"] == 498


def test_relaxation_profile_reports_catch_all_regex(tmp_path):
    fv_solution = tmp_path / "fvSolution"
    fv_solution.write_text(
        """
relaxationFactors
{
    equations
    {
        U               0.7;
        ".*"            0.5;
    }
}
"""
    )

    text = formal.relaxation_factors_equations_text(fv_solution)
    entries = formal.relaxation_entries_from_text(text)

    assert "equations" in text
    assert entries == {
        "U_exact_entry": 0.7,
        "equation_catch_all_regex": ".*",
        "equation_catch_all_value": 0.5,
    }


def test_formal_comparison_status_requires_quality_gates(tmp_path):
    root = tmp_path / "selected"
    cases = {}
    required_gates = {
        "blockMesh": True,
        "checkMesh": True,
        "pair_audit": True,
        "simpleFoam": True,
        "no_solver_failures": True,
        "final_fields": True,
        "residual_parser": True,
        "flow_balance": True,
        "yPlus": True,
        "wallShearStress": True,
        "patch_pressure": True,
        "simple_solution_converged": True,
    }
    for model in ["kEpsilon", "kOmegaSST"]:
        case_dir = root / model
        result_dir = case_dir / "results"
        result_dir.mkdir(parents=True)
        (result_dir / "solver_summary.json").write_text(
            json.dumps(
                {
                    "status": "converged",
                    "quality_gate_status": "passed",
                    "quality_gates": required_gates,
                }
            )
        )
        cases[model] = case_dir

    assert formal.required_quality_gate_report(cases)["comparison_status"] == "formal_comparison"

    broken = json.loads((cases["kOmegaSST"] / "results/solver_summary.json").read_text())
    broken["quality_gates"]["wallShearStress"] = False
    (cases["kOmegaSST"] / "results/solver_summary.json").write_text(json.dumps(broken))

    assert formal.required_quality_gate_report(cases)["comparison_status"] == "quality_incomplete_comparison"


def test_v1_hash_plan_includes_expected_files():
    paths = formal.v1_hash_targets(Path("runs/rans_pitzdaily_formal"))

    assert any(path.as_posix().endswith("kEpsilon/results/residuals.csv") for path in paths)
    assert any(path.as_posix().endswith("kOmegaSST/results/wall_shear_stress_values.csv") for path in paths)
    assert any(path.as_posix().endswith("comparison/model_summary.csv") for path in paths)
    assert any(path.as_posix().endswith("comparison/reattachment_summary.csv") for path in paths)


def test_quality_gate_does_not_pass_or_fail_on_yplus_numeric_size():
    gate_low = formal.quality_gate_status(
        {
            "blockMesh": True,
            "checkMesh": True,
            "pair_audit": True,
            "simpleFoam": True,
            "no_solver_failures": True,
            "final_fields": True,
            "residual_parser": True,
            "flow_balance": True,
            "yPlus": True,
            "wallShearStress": True,
            "patch_pressure": True,
            "simple_solution_converged": False,
        },
        yplus_summary={"lowerWall_p95": 1.0},
    )
    gate_high = formal.quality_gate_status(
        {
            "blockMesh": True,
            "checkMesh": True,
            "pair_audit": True,
            "simpleFoam": True,
            "no_solver_failures": True,
            "final_fields": True,
            "residual_parser": True,
            "flow_balance": True,
            "yPlus": True,
            "wallShearStress": True,
            "patch_pressure": True,
            "simple_solution_converged": False,
        },
        yplus_summary={"lowerWall_p95": 1000.0},
    )

    assert gate_low == gate_high == "incomplete_convergence"


def test_comparison_columns_have_no_winner_or_accuracy_fields():
    forbidden = {"winner", "accuracy", "best_model"}

    assert forbidden.isdisjoint(set(formal.MODEL_SUMMARY_FIELDS))


def test_common_hash_pair_remains_consistent(tmp_path):
    eps_case = tmp_path / "kEpsilon"
    sst_case = tmp_path / "kOmegaSST"
    prepare.prepare_case("kEpsilon", eps_case, max_iterations=50)
    prepare.prepare_case("kOmegaSST", sst_case, max_iterations=50)

    report = pair_audit.audit_pair(eps_case, sst_case)

    assert report["pair_audit_passed"]
    assert report["common_file_hashes_match"]


def test_formal_script_and_comparison_cli_exist():
    formal_script = ROOT / "scripts/run_rans_pitzdaily_formal.sh"
    compare_script = ROOT / "scripts/compare_rans_pitzdaily_models.py"

    assert formal_script.is_file()
    assert compare_script.is_file()
    formal_text = formal_script.read_text()
    assert "runs/rans_pitzdaily_formal_v2" in formal_text
    assert "potentialFoam -case" not in formal_text
    assert "potentialFlow" not in formal_text
    assert "Phi" not in formal_text
    assert 'simpleFoam -case "$case_dir" -postProcess' in formal_text
    assert "--output-root" in compare_script.read_text()
