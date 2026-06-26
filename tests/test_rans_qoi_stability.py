from __future__ import annotations

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


qoi = load_module(ROOT / "scripts/audit_rans_qoi_stability.py")
continuation = load_module(ROOT / "scripts/run_rans_qoi_stability_continuation.py")


def test_explicit_time_postprocess_command_uses_time():
    command = qoi.explicit_time_postprocess_command(Path("case"), "700", "wallShearStress")

    assert command == ["simpleFoam", "-case", "case", "-postProcess", "-time", "700", "-func", "wallShearStress"]
    assert "-latestTime" not in command
    assert "-postProcess" in command
    patch_command = qoi.explicit_time_postprocess_command(Path("case"), "700", "patchAverage(p,patch=outlet)")
    assert patch_command == ["postProcess", "-case", "case", "-time", "700", "-func", "patchAverage(p,patch=outlet)"]


def test_qoi_relative_change_helpers():
    assert qoi.relative_change(100.0, 101.0) == 1.0 / 101.0
    assert qoi.relative_change(6.0, 6.06) < 0.02
    assert qoi.relative_change(6.0, 6.3) > 0.02


def test_wall_shear_curve_relative_l2():
    value = qoi.relative_l2([1.0, 2.0, 3.0], [1.0, 2.0, 3.3])

    assert value is not None
    assert 0.0 < value < 0.1


def test_stability_gate_thresholds_and_continuation_plan():
    passed = {
        "pressure_recovery": 10.0,
        "reattachment_length": 5.0,
        "lower_yplus_median": 20.0,
        "lower_yplus_p95": 30.0,
        "flow_imbalance": 0.005,
        "lower_wall_shear_curve": [1.0, 2.0, 3.0],
    }
    previous = {
        "pressure_recovery": 9.9,
        "reattachment_length": 4.95,
        "lower_yplus_median": 19.8,
        "lower_yplus_p95": 29.7,
        "flow_imbalance": 0.006,
        "lower_wall_shear_curve": [1.0, 2.0, 2.98],
    }
    row = qoi.stability_row(
        "kEpsilon",
        previous,
        passed,
        {"comparison_iteration": "700", "final_iteration": "798", "actual_offset_iterations": 98},
    )

    assert row["pressure_recovery_gate"] is True
    assert row["reattachment_gate"] is True
    assert row["lower_yplus_median_gate"] is True
    assert row["lower_yplus_p95_gate"] is True
    assert row["wall_shear_curve_gate"] is True
    assert row["final_flow_imbalance_gate"] is True
    assert row["qoi_stability_passed"] is True
    assert qoi.continuation_plan_required([row]) is False

    row["reattachment_relative_change"] = 0.05
    row["reattachment_gate"] = False
    row["qoi_stability_passed"] = False
    assert qoi.continuation_plan_required([row]) is True
    plan = qoi.continuation_plan_for_both_models(Path("runs/root"), "conservative_common")
    assert plan["models"] == ["kEpsilon", "kOmegaSST"]
    assert plan["additional_iterations"] == 300
    assert plan["same_settings_for_both_models"] is True

    no_recursive_plan = qoi.continuation_plan_for_both_models(
        Path("runs/root/stability_continuation"),
        "continuation_common",
    )
    assert no_recursive_plan["required"] is False
    assert no_recursive_plan["allowed"] is False


def test_continuation_rewrites_control_dict_for_fixed_extension(tmp_path):
    control = tmp_path / "controlDict"
    control.write_text(
        """
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         8000;
writeControl    timeStep;
writeInterval   50;
""",
        encoding="utf-8",
    )

    end_time = continuation.configure_control_dict(
        control,
        model="kEpsilon",
        final_time=798.0,
        additional_iterations=300,
        write_interval=100,
    )
    text = control.read_text(encoding="utf-8")

    assert end_time == "1098"
    assert "startFrom       latestTime;" in text
    assert "startTime       798;" in text
    assert "endTime         1098;" in text
    assert "writeInterval   1;" in text
    assert "writeContinuationFinalObjects" not in text


def test_continuation_prunes_intermediate_time_dirs_but_keeps_qoi_times(tmp_path):
    case_dir = tmp_path / "case"
    for name in ["798", "799", "800", "900", "998", "999", "1000", "1097", "1098"]:
        (case_dir / name).mkdir(parents=True)

    removed = continuation.prune_continuation_time_dirs(
        case_dir,
        initial_final=798.0,
        continuation_end=1098.0,
        retained_interval=100,
    )
    kept = sorted(path.name for path in case_dir.iterdir())

    assert "799" in removed
    assert "999" in removed
    assert "1097" in removed
    assert kept == ["1000", "1098", "798", "800", "900", "998"]


def test_continuation_disables_residual_control_without_changing_relaxation(tmp_path):
    fv_solution = tmp_path / "fvSolution"
    fv_solution.write_text(
        """
SIMPLE
{
    nNonOrthogonalCorrectors 0;
    residualControl
    {
        p               1e-2;
        U               1e-3;
    }
}
relaxationFactors
{
    equations
    {
        U               0.7;
        ".*"            0.5;
    }
}
""",
        encoding="utf-8",
    )

    assert continuation.disable_residual_control(fv_solution) is True
    text = fv_solution.read_text(encoding="utf-8")

    assert "residualControl" in text
    assert "p               1e-2;" not in text
    assert "U               0.7;" in text
    assert '".*"            0.5;' in text


def test_continuation_output_path_is_restricted():
    allowed = ROOT / "runs/rans_pitzdaily_formal_v2/stability_continuation/continuation_common"
    assert continuation.ensure_within_runs(allowed) == allowed.resolve()

    outside = ROOT / "runs/rans_pitzdaily_formal_v2/conservative_common"
    try:
        continuation.ensure_within_runs(outside)
    except ValueError as exc:
        assert "Refusing continuation output outside" in str(exc)
    else:
        raise AssertionError("Expected continuation output guard to reject non-continuation path")
