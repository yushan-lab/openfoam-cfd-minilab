from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_generate_cavity_mesh_supports_four_resolutions_and_preserves_patches():
    module = load_module(ROOT / "scripts/generate_cavity_mesh.py")

    for resolution in [20, 40, 80, 160]:
        text = module.render_block_mesh_dict(resolution)
        assert f"({resolution} {resolution} 1)" in text
        assert "frontAndBack" in text
        assert "type empty;" in text
        assert text.count("type wall;") == 4
        assert module.cell_count_for_resolution(resolution) == resolution * resolution


def test_generate_cavity_mesh_rejects_invalid_resolution():
    module = load_module(ROOT / "scripts/generate_cavity_mesh.py")

    for resolution in [10, 60, 81]:
        try:
            module.validate_resolution(resolution)
        except ValueError as exc:
            assert "Allowed resolutions" in str(exc)
        else:
            raise AssertionError(f"resolution {resolution} should have failed")


def test_generate_cavity_mesh_cli_writes_expected_dictionary():
    with TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "blockMeshDict"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/generate_cavity_mesh.py"),
                "--resolution",
                "80",
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0
        assert "(80 80 1)" in output.read_text()


def test_run_cavity_script_supports_output_root_isolation_and_overwrite_gate():
    script = (ROOT / "scripts/run_cavity.sh").read_text()

    assert 'OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR}"' in script
    assert 'END_TIME="${END_TIME:-5}"' in script
    assert 'DELTA_T="${DELTA_T:-0.0025}"' in script
    assert 'COURANT_LIMIT="${COURANT_LIMIT:-0.5}"' in script
    assert 'WRITE_INTERVAL_TIME="${WRITE_INTERVAL_TIME:-0.5}"' in script
    assert 'OVERWRITE="${OVERWRITE:-0}"' in script
    assert 'START_FROM_LATEST="${START_FROM_LATEST:-0}"' in script
    assert 'PURGE_WRITE="${PURGE_WRITE:-3}"' in script
    assert 'SAVE_FINAL_FIELDS="${SAVE_FINAL_FIELDS:-auto}"' in script
    assert 'SAVE_FINAL_MINUS_INTERVAL="${SAVE_FINAL_MINUS_INTERVAL:-0}"' in script
    assert 'FINAL_FIELD_DIR="$OUTPUT_ROOT/final_fields"' in script
    assert 'deltaT          $DELTA_T;' in script
    assert 'adjustTimeStep  no;' in script
    assert 'writeInterval   $WRITE_INTERVAL_STEPS;' in script
    assert 'maxCo           $' not in script
    assert 'START_FROM_SETTING="latestTime"' in script
    assert 'startFrom       $START_FROM_SETTING;' in script
    assert 'purgeWrite      $PURGE_WRITE;' in script
    assert 'RUN_DIR_ISOLATED=1' in script
    assert 'OVERWRITE=1' in script
    assert 'Refusing to overwrite non-empty OUTPUT_ROOT' in script
    assert 'scripts/generate_cavity_mesh.py --resolution "$MESH_RESOLUTION"' in script
    assert 'OUTPUT_ROOT="$ROOT_DIR/results/public"' not in script


def test_run_cavity_metadata_records_solver_step_count_from_log():
    script = (ROOT / "scripts/run_cavity.sh").read_text()

    assert '"number_of_steps": None' not in script
    assert 'ICOFOAM_LOG="$LOG_DIR/icoFoam.log"' in script
    assert 'time_values = []' in script
    assert '"requested_deltaT": float(os.environ["DELTA_T"])' in script
    assert '"observed_deltaT_min": min(deltas) if deltas else None' in script
    assert '"observed_maxCo": observed_max_co' in script
    assert '"courant_gate_passed": None if observed_max_co is None else observed_max_co <= courant_limit' in script


def test_plot_residuals_parses_final_residuals_continuity_and_failures():
    module = load_module(ROOT / "scripts/plot_residuals.py")
    log_text = """
Time = 0.005
smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 8e-06, No Iterations 1
smoothSolver:  Solving for Uy, Initial residual = 0.03, Final residual = 7e-06, No Iterations 1
DICPCG:  Solving for p, Initial residual = 0.2, Final residual = 9e-07, No Iterations 12
time step continuity errors : sum local = 1e-10, global = -2e-12, cumulative = 3e-11
"""

    diagnostics = module.parse_solver_log(log_text)

    assert diagnostics.residuals[0].field == "Ux"
    assert diagnostics.residuals[0].initial_residual == 0.12
    assert diagnostics.residuals[0].final_residual == 8e-06
    assert diagnostics.continuity_errors[0].sum_local == 1e-10
    assert diagnostics.continuity_errors[0].global_error == -2e-12
    assert diagnostics.has_failure is False

    failed = module.parse_solver_log("Time = 1\nFloating point exception\n")
    assert failed.has_failure
    assert "Floating point exception" in failed.failure_reasons


def test_plot_residuals_does_not_treat_openfoam_sigfpe_setup_as_failure():
    module = load_module(ROOT / "scripts/plot_residuals.py")

    diagnostics = module.parse_solver_log(
        """
sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).
Time = 1
smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 0.01, No Iterations 1
"""
    )

    assert diagnostics.has_failure is False


def test_plot_residuals_writes_continuity_csv_and_solver_summary():
    module = load_module(ROOT / "scripts/plot_residuals.py")
    diagnostics = module.parse_solver_log(
        """
Time = 1
smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 0.01, No Iterations 1
time step continuity errors : sum local = 2e-10, global = 3e-11, cumulative = 4e-10
"""
    )

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        continuity_csv = temp_path / "continuity_errors.csv"
        summary_json = temp_path / "solver_summary.json"

        module.write_continuity_csv(continuity_csv, diagnostics.continuity_errors)
        module.write_solver_summary_json(summary_json, diagnostics)

        assert continuity_csv.read_text().splitlines()[0] == "time,sum_local,global_error,cumulative"
        summary = json.loads(summary_json.read_text())
        assert summary["has_failure"] is False
        assert summary["final_residuals"]["Ux"]["final_residual"] == 0.01


def test_steady_state_relative_l2_change():
    module = load_module(ROOT / "scripts/check_cavity_steady_state.py")

    change = module.relative_l2_change(
        [(1.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
        [(0.9, 0.0, 0.0), (0.0, 1.8, 0.0)],
    )

    assert math.isclose(change, math.sqrt(0.05) / math.sqrt(5.0))


def test_evaluate_validation_interpolates_and_computes_metrics():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    cfd = [(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)]
    reference = [(0.25, 0.25), (0.75, 0.25)]
    compared = module.compare_profile(cfd, reference)

    assert compared.rmse == 0.25
    assert compared.l_inf == 0.25


def test_evaluate_validation_skips_reference_points_outside_cell_centered_range():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    cfd = [(0.025, 0.0), (0.5, 0.4), (0.975, 1.0)]
    reference = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
    compared = module.compare_profile(cfd, reference)
    rows, comparisons = module.build_prediction_rows_from_profiles(
        resolution=20,
        profiles={"u": cfd, "v": cfd},
        reference_profiles={"u": reference, "v": reference},
    )

    assert compared.count == 1
    assert compared.rmse == 0.09999999999999998
    assert comparisons["u"].count == 1
    assert [(row["profile"], row["coordinate"]) for row in rows] == [("u", 0.5), ("v", 0.5)]


def test_evaluate_validation_refuses_missing_reference_by_default():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        try:
            module.load_reference_profiles(Path(temp_dir) / "missing")
        except FileNotFoundError as exc:
            assert "Reference centerline files are missing" in str(exc)
        else:
            raise AssertionError("missing reference data should fail")


def test_evaluate_validation_requires_reference_source_metadata():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        reference_dir = Path(temp_dir)
        (reference_dir / "re100_centerline_u.csv").write_text("y,Ux\n0.5,0.2\n")
        (reference_dir / "re100_centerline_v.csv").write_text("x,Uy\n0.5,-0.1\n")
        (reference_dir / "source.json").write_text(json.dumps({"title": "reference"}))

        try:
            module.load_reference_profiles(reference_dir)
        except ValueError as exc:
            assert "source.json is missing required metadata" in str(exc)
        else:
            raise AssertionError("incomplete reference source metadata should fail")


def test_reference_data_files_have_required_format_and_checksums():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")
    reference_dir = ROOT / "data" / "reference"
    u_path = reference_dir / "re100_centerline_u.csv"
    v_path = reference_dir / "re100_centerline_v.csv"
    source_path = reference_dir / "source.json"

    assert u_path.exists()
    assert v_path.exists()
    assert source_path.exists()

    source = json.loads(source_path.read_text())
    assert source["title"] == "High-Re Solutions for Incompressible Flow Using the Navier-Stokes Equations and a Multigrid Method"
    assert source["year"] == 1982
    assert source["doi"] == "10.1016/0021-9991(82)90058-4"
    assert source["accessed"] == "2026-06-25"

    profiles = module.load_reference_profiles(reference_dir)
    assert len(profiles["u"]) == 17
    assert len(profiles["v"]) == 17
    assert u_path.read_text().splitlines()[0] == "y,Ux"
    assert v_path.read_text().splitlines()[0] == "x,Uy"
    assert profiles["u"][0] == (1.0, 1.0)
    assert profiles["v"][8] == (0.5, 0.05454)

    for path in [u_path, v_path]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert source["checksum"][path.name] == f"sha256:{digest}"


def test_evaluate_validation_computes_metrics_and_prediction_table_from_run_outputs():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "runs" / "cavity_validation" / "N20"
        (run_dir / "metadata").mkdir(parents=True)
        (run_dir / "results").mkdir()
        (run_dir / "metadata" / "run_metadata.json").write_text(
            json.dumps(
                {
                    "resolution": 20,
                    "status": "completed",
                    "cell_count": 400,
                    "dx": 0.05,
                    "final_time": 20.0,
                    "relative_L2_change": 4e-6,
                    "wall_clock_seconds": 12.5,
                }
            )
        )
        (run_dir / "results" / "centerline_u.csv").write_text(
            "y,Ux\n0.0,0.0\n0.5,0.4\n1.0,1.0\n"
        )
        (run_dir / "results" / "centerline_v.csv").write_text(
            "x,Uy\n0.0,0.0\n0.5,-0.2\n1.0,0.0\n"
        )
        (run_dir / "results" / "solver_summary.json").write_text(
            json.dumps(
                {
                    "final_residuals": {
                        "Ux": {"final_residual": 1e-7},
                        "Uy": {"final_residual": 2e-7},
                        "p": {"final_residual": 3e-7},
                    },
                    "final_continuity_error": {"cumulative": 4e-9},
                }
            )
        )

        reference_dir = root / "data" / "reference"
        reference_dir.mkdir(parents=True)
        (reference_dir / "re100_centerline_u.csv").write_text("y,Ux\n0.5,0.5\n")
        (reference_dir / "re100_centerline_v.csv").write_text("x,Uy\n0.5,-0.1\n")
        (reference_dir / "source.json").write_text(
            json.dumps(
                {
                    "title": "Verified cavity reference",
                    "authors": ["A. Author"],
                    "year": 1982,
                    "source_url": "https://example.test/reference",
                    "accessed": "2026-06-25",
                    "checksum": "synthetic-test-data",
                }
            )
        )

        output_dir = root / "results" / "public"
        records = module.evaluate_runs(
            root / "runs" / "cavity_validation",
            reference_dir,
            output_dir,
            figures_dir=root / "figures",
        )

        assert records[0]["RMSE_U"] == 0.09999999999999998
        assert records[0]["L_inf_U"] == 0.09999999999999998
        assert records[0]["RMSE_V"] == 0.1
        assert records[0]["L_inf_V"] == 0.1
        assert records[0]["final_residual"] == 3e-7
        assert records[0]["continuity_error"] == 4e-9

        summary_rows = list(csv.DictReader((output_dir / "cavity_grid_summary.csv").open()))
        assert summary_rows[0]["reference_status"] == "available"
        assert summary_rows[0]["final_residual"] == "3e-07"

        prediction_rows = list(
            csv.DictReader((output_dir / "cavity_centerline_predictions.csv").open())
        )
        assert prediction_rows[0].keys() >= {
            "resolution",
            "profile",
            "coordinate",
            "cfd_value",
            "reference_value",
            "error",
        }
        assert {row["profile"] for row in prediction_rows} == {"u", "v"}
        for figure_name in [
            "cavity_centerline_validation.png",
            "cavity_grid_error.png",
            "cavity_error_vs_cost.png",
            "cavity_residuals_by_grid.png",
        ]:
            assert (root / "figures" / figure_name).is_file()


def test_grid_summary_keeps_failed_run_manifest_rows():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "runs" / "cavity_validation" / "N20"
        (run_dir / "metadata").mkdir(parents=True)
        (run_dir / "results").mkdir()
        (run_dir / "metadata" / "run_metadata.json").write_text(
            json.dumps(
                {
                    "resolution": 20,
                    "status": "failed",
                    "cell_count": 400,
                    "dx": 0.05,
                    "final_time": None,
                    "relative_L2_change": None,
                    "final_residual": None,
                    "continuity_error": None,
                    "wall_clock_seconds": 1.5,
                }
            )
        )

        output = root / "results" / "public" / "cavity_grid_summary.csv"
        module.write_grid_summary(output, [module.load_run_record(run_dir)])

        rows = list(csv.DictReader(output.open()))
        assert rows[0]["resolution"] == "20"
        assert rows[0]["status"] == "failed"


def test_evaluate_validation_keeps_manifest_failure_without_run_directory():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        runs_root = root / "runs" / "cavity_validation"
        runs_root.mkdir(parents=True)
        (runs_root / "manifest.csv").write_text(
            "resolution,status,output_root\n"
            f"20,failed,{runs_root / 'N20'}\n"
        )

        output_dir = root / "results" / "public"
        records = module.evaluate_runs(
            runs_root,
            root / "data" / "reference",
            output_dir,
            allow_missing_reference=True,
            figures_dir=root / "figures",
        )

        assert records == [
            {
                "resolution": 20,
                "status": "failed",
                "run_dir": str(runs_root / "N20"),
                "reference_status": "missing",
            }
        ]
        rows = list(csv.DictReader((output_dir / "cavity_grid_summary.csv").open()))
        assert rows[0]["resolution"] == "20"
        assert rows[0]["status"] == "failed"


def test_evaluate_validation_resolves_manifest_output_root_to_local_run_dir_when_available():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        runs_root = root / "runs" / "cavity_validation"
        run_dir = runs_root / "N20"
        (run_dir / "metadata").mkdir(parents=True)
        (run_dir / "results").mkdir(parents=True)
        (runs_root / "manifest.csv").write_text(
            "resolution,status,output_root\n"
            "20,completed,/mnt/d/nonportable/path/N20\n"
        )
        (run_dir / "metadata" / "run_metadata.json").write_text(
            json.dumps({"resolution": 20, "status": "completed", "cell_count": 400})
        )

        records = module.load_run_records(runs_root)

        assert records[0]["run_dir"] == str(run_dir)
        assert records[0]["cell_count"] == 400


def test_grid_figures_are_generated_from_summary_and_prediction_csv_files():
    module = load_module(ROOT / "scripts/evaluate_cavity_validation.py")

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        summary_csv = root / "results" / "public" / "cavity_grid_summary.csv"
        predictions_csv = root / "results" / "public" / "cavity_centerline_predictions.csv"
        figures_dir = root / "figures"
        summary_csv.parent.mkdir(parents=True)
        summary_csv.write_text(
            "resolution,status,cell_count,dx,final_time,relative_L2_change,"
            "final_residual,continuity_error,wall_clock_seconds,RMSE_U,L_inf_U,RMSE_V,L_inf_V,reference_status\n"
            "20,completed,400,0.05,20,1e-6,3e-7,4e-9,12.5,0.1,0.1,0.2,0.2,available\n"
        )
        predictions_csv.write_text(
            "resolution,profile,coordinate,cfd_value,reference_value,error\n"
            "20,u,0.5,0.4,0.5,-0.1\n"
            "20,v,0.5,-0.2,-0.1,-0.1\n"
        )

        module.write_grid_figures_from_csv(summary_csv, predictions_csv, figures_dir)

        for figure_name in [
            "cavity_centerline_validation.png",
            "cavity_grid_error.png",
            "cavity_error_vs_cost.png",
            "cavity_residuals_by_grid.png",
        ]:
            assert (figures_dir / figure_name).is_file()


def test_grid_study_script_contract():
    script = (ROOT / "scripts/run_cavity_grid_study.sh").read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in script
    assert 'RESOLUTIONS="${RESOLUTIONS:-20 40 80 160}"' in script
    assert 'END_TIME="${END_TIME:-20}"' in script
    assert 'MAX_END_TIME="${MAX_END_TIME:-50}"' in script
    assert 'EXTEND_TIME="${EXTEND_TIME:-10}"' in script
    assert 'DELTA_T="${DELTA_T:-0.0025}"' in script
    assert 'COURANT_LIMIT="${COURANT_LIMIT:-0.5}"' in script
    assert 'STEADY_THRESHOLD="${STEADY_THRESHOLD:-1e-5}"' in script
    assert 'OVERWRITE="${OVERWRITE:-0}"' in script
    assert 'PYTHON_BIN="${PYTHON:-python3}"' in script
    assert 'OUTPUT_ROOT="$RUN_ROOT/N$resolution"' in script
    assert 'OVERWRITE="$OVERWRITE"' in script
    assert 'START_FROM_LATEST=1' in script
    assert 'OVERWRITE=1' in script
    assert "grid_start_epoch" in script
    assert "total_number_of_steps" in script
    assert "update_grid_metadata_totals" in script
    assert "check_run_outputs" in script
    assert 'test -s "$OUTPUT_ROOT/logs/blockMesh.log"' in script
    assert 'test -s "$OUTPUT_ROOT/results/solver_summary.json"' in script
    assert 'steady_state.json' in script
    assert 'not_converged' in script
    assert "manifest.csv" in script
    assert 'echo "$resolution,failed,$OUTPUT_ROOT" >> "$MANIFEST"' in script
    assert "evaluate_cavity_validation.py" in script
    assert '--figures-dir "$ROOT_DIR/figures"' in script


def test_makefile_preserves_legacy_run20_and_run40_targets():
    makefile = (ROOT / "Makefile").read_text()

    assert "run20:" in makefile
    assert "MESH_RESOLUTION=20 bash scripts/run_cavity.sh" in makefile
    assert "run40:" in makefile
    assert "MESH_RESOLUTION=40 bash scripts/run_cavity.sh" in makefile


def test_diagnostics_sample_dict_uses_points_and_cellpoint_interpolation():
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")

    text = module.exact_sample_dict([(0.0, 0.0), (0.5, 1.0)], [(0.25, 0.0)])

    assert "interpolationScheme cellPoint;" in text
    assert "type points;" in text
    assert "(0.5 0 0)" in text
    assert "(0.5 0.5 0)" in text
    assert "(0.25 0.5 0)" in text


def test_diagnostics_courant_parser_extracts_max_co_and_time_steps(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    log_path = tmp_path / "icoFoam.log"
    log_path.write_text(
        "Time = 0.005s\n"
        "Courant Number mean: 0.1 max: 0.4\n"
        "Time = 0.01s\n"
        "Courant Number mean: 0.2 max: 0.6\n"
    )

    parsed = module.parse_courant_log(log_path)

    assert parsed["deltaT_min"] == 0.005
    assert parsed["max_Co_max"] == 0.6
    assert parsed["max_Co_exceeded_0p5"] is True
    assert parsed["time_steps_in_log"] == 2


def test_diagnostics_freeze_hashes_marks_missing_final_fields(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    runs_root = tmp_path / "runs"
    diagnostics = runs_root / "diagnostics"
    for resolution in [20, 40, 80, 160]:
        run = runs_root / f"N{resolution}"
        (run / "results").mkdir(parents=True)
        (run / "logs").mkdir()
        (run / "results" / "centerline_u.csv").write_text("y,Ux\n0.5,0\n")
        (run / "results" / "centerline_v.csv").write_text("x,Uy\n0.5,0\n")
        (run / "logs" / "icoFoam.log").write_text("End\n")
    (tmp_path / "results/public").mkdir(parents=True)
    (tmp_path / "results/public/cavity_grid_summary.csv").write_text("resolution\n20\n")
    old_root = module.ROOT
    old_reference_u = module.REFERENCE_U
    old_reference_v = module.REFERENCE_V
    module.ROOT = tmp_path
    module.REFERENCE_U = ROOT / "data/reference/re100_centerline_u.csv"
    module.REFERENCE_V = ROOT / "data/reference/re100_centerline_v.csv"
    try:
        payload = module.freeze_hashes(runs_root, diagnostics)
    finally:
        module.ROOT = old_root
        module.REFERENCE_U = old_reference_u
        module.REFERENCE_V = old_reference_v

    assert payload["runs"]["N20"]["final_U"]["status"] == "missing"
    assert (diagnostics / "original_result_hashes.json").exists()


def test_diagnostics_freeze_hashes_uses_diagnostic_final_field_fallback(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    runs_root = tmp_path / "runs"
    diagnostics = runs_root / "diagnostics"
    for resolution in [20, 40, 80, 160]:
        run = runs_root / f"N{resolution}"
        (run / "results").mkdir(parents=True)
        (run / "logs").mkdir()
        (run / "results" / "centerline_u.csv").write_text("y,Ux\n0.5,0\n")
        (run / "results" / "centerline_v.csv").write_text("x,Uy\n0.5,0\n")
        (run / "logs" / "icoFoam.log").write_text("End\n")
        final_fields = diagnostics / "exact_runs" / f"N{resolution}" / "final_fields"
        final_fields.mkdir(parents=True)
        (final_fields / "U").write_text(f"U {resolution}\n")
        (final_fields / "p").write_text(f"p {resolution}\n")
    (tmp_path / "results/public").mkdir(parents=True)
    (tmp_path / "results/public/cavity_grid_summary.csv").write_text("resolution\n20\n")
    old_root = module.ROOT
    old_reference_u = module.REFERENCE_U
    old_reference_v = module.REFERENCE_V
    module.ROOT = tmp_path
    module.REFERENCE_U = ROOT / "data/reference/re100_centerline_u.csv"
    module.REFERENCE_V = ROOT / "data/reference/re100_centerline_v.csv"
    try:
        payload = module.freeze_hashes(runs_root, diagnostics)
    finally:
        module.ROOT = old_root
        module.REFERENCE_U = old_reference_u
        module.REFERENCE_V = old_reference_v

    final_u = payload["runs"]["N20"]["final_U"]
    assert final_u["source"] == "diagnostic_exact_rerun_matching_original_parameters"
    assert final_u["sha256"].startswith("sha256:")


def test_diagnostics_writes_combined_centerline_profile_table(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    rows = module.profile_table_rows(40, "exact_original", [(0.25, 0.1), (0.75, 0.9)], "y", "Ux")
    output = tmp_path / "centerline_u_exact_sample.csv"

    module.write_centerline_profile_table(output, rows, "y", "Ux")

    written = list(csv.DictReader(output.open()))
    assert written[0] == {
        "resolution": "40",
        "phase": "exact_original",
        "y": "0.25",
        "Ux": "0.1",
    }


def _write_vector_field(path: Path, values: list[tuple[float, float, float]]) -> None:
    body = "\n".join(f"({ux} {uy} {uz})" for ux, uy, uz in values)
    path.write_text(
        "FoamFile\n{\n    class volVectorField;\n    object U;\n}\n"
        f"internalField nonuniform List<vector>\n{len(values)}\n(\n{body}\n)\n;\n"
    )


def test_diagnostics_computes_steady_row_from_case_time_dirs(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    case_dir = tmp_path / "case"
    for time_value, scale in [(0, 0.0), (5, 0.5), (8, 0.8), (8.5, 0.85), (9, 0.9), (9.5, 0.95), (10, 1.0)]:
        time_dir = case_dir / f"{time_value:g}"
        time_dir.mkdir(parents=True)
        _write_vector_field(
            time_dir / "U",
            [
                (scale, 0.0, 0.0),
                (scale + 0.1, 0.0, 0.0),
                (scale, -scale, 0.0),
                (scale + 0.1, -scale, 0.0),
            ],
        )
    output_root = tmp_path / "output"
    (output_root / "results").mkdir(parents=True)
    (output_root / "results/solver_summary.json").write_text(
        json.dumps(
            {
                "final_residuals": {
                    "Ux": {"final_residual": 1e-6},
                    "Uy": {"final_residual": 2e-6},
                    "p": {"final_residual": 3e-6},
                },
                "final_continuity_error": {"cumulative": 4e-9},
            }
        )
    )

    row = module.diagnostic_steady_row_from_case(2, "diagnostic_original_rerun", output_root, case_dir)

    assert row["latest_time"] == 10.0
    assert row["previous_time"] == 9.5
    assert row["fixed_5_actual_interval"] == 5.0
    assert row["fixed_5_status"] == "available"
    assert "5->8:" in row["last_5_write_relative_L2_changes"]
    assert "8->10:" in row["centerline_change_last_20_percent"]
    assert row["final_residual"] == 3e-6
    assert row["continuity_error"] == 4e-9


def test_diagnostics_version_audit_records_openfoam10_metadata(tmp_path):
    module = load_module(ROOT / "scripts/diagnose_cavity_grid.py")
    runs_root = tmp_path / "runs"
    for resolution in [20, 40, 80, 160]:
        metadata_dir = runs_root / f"N{resolution}" / "metadata"
        metadata_dir.mkdir(parents=True)
        (metadata_dir / "run_metadata.json").write_text(json.dumps({"openfoam_version": "10"}))
    (tmp_path / "results/public").mkdir(parents=True)
    old_root = module.ROOT
    module.ROOT = tmp_path
    try:
        rows = module.write_version_consistency_audit(runs_root, tmp_path / "diagnostics")
    finally:
        module.ROOT = old_root

    assert all(row["version_status"] == "OpenFOAM-10" for row in rows[:4])
    assert rows[-1]["version_status"] == "no_version_column_public_csv_left_unmodified"


def test_validation_v2_contract_uses_fixed_timestep_and_exact_sampling_only():
    script = (ROOT / "scripts/run_cavity_validation_v2.py").read_text()
    run_script = (ROOT / "scripts/run_cavity.sh").read_text()

    assert 'DELTA_T = 0.0025' in script
    assert 'WRITE_INTERVAL_TIME = 0.5' in script
    assert '"DELTA_T": f"{DELTA_T:g}"' in script
    assert '"WRITE_INTERVAL_TIME": f"{WRITE_INTERVAL_TIME:g}"' in script
    assert "centerline_u_exact.csv" in script
    assert "centerline_v_exact.csv" in script
    assert "centerline_u.csv" not in script.split("def metrics_for_run", 1)[1].split("def build_validation_summary", 1)[0]
    assert 'adjustTimeStep  no;' in run_script
    assert 'writeInterval   $WRITE_INTERVAL_STEPS;' in run_script


def test_validation_v2_courant_parser_and_quality_gate():
    module = load_module(ROOT / "scripts/run_cavity_validation_v2.py")
    with TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "icoFoam.log"
        log_path.write_text(
            "Time = 0.0025s\n"
            "Courant Number mean: 0.1 max: 0.4\n"
            "Time = 0.005s\n"
            "Courant Number mean: 0.2 max: 0.6\n"
        )

        parsed = module.parse_icofoam_log(log_path)

    assert parsed["observed_deltaT_min"] == 0.0025
    assert parsed["observed_deltaT_max"] == 0.0025
    assert parsed["observed_maxCo"] == 0.6
    assert module.quality_gate_reasons(
        observed_max_co=0.6,
        steady_relative_l2=1e-6,
        sample_count_u=17,
        sample_count_v=17,
    ) == ["courant_gate_failed"]


def test_validation_v2_fixed_interval_l2_uses_five_physical_time_units(tmp_path):
    module = load_module(ROOT / "scripts/run_cavity_validation_v2.py")
    case_dir = tmp_path / "case"
    for time_value, scale in [(15, 0.5), (20, 1.0)]:
        time_dir = case_dir / f"{time_value:g}"
        time_dir.mkdir(parents=True)
        _write_vector_field(
            time_dir / "U",
            [
                (scale, 0.0, 0.0),
                (scale, 0.0, 0.0),
                (scale, 0.0, 0.0),
                (scale, 0.0, 0.0),
            ],
        )

    steady = module.fixed_interval_l2(case_dir, resolution=2, interval=5.0)

    assert steady["actual_interval"] == 5.0
    assert steady["relative_l2"] == 0.5
    assert module.quality_gate_reasons(
        observed_max_co=0.4,
        steady_relative_l2=steady["relative_l2"],
        sample_count_u=17,
        sample_count_v=17,
    ) == ["steady_gate_failed"]


def test_validation_v2_exact_sample_count_gate_and_reference_points():
    module = load_module(ROOT / "scripts/run_cavity_validation_v2.py")
    sample_dict = module.exact_sample_dict(
        module.read_profile(module.REFERENCE_U),
        module.read_profile(module.REFERENCE_V),
    )

    assert sample_dict.count("verticalCenterline") == 1
    assert sample_dict.count("horizontalCenterline") == 1
    assert sample_dict.count("(0.5 ") >= 17
    assert "interpolationScheme cellPoint;" in sample_dict
    assert module.quality_gate_reasons(
        observed_max_co=0.4,
        steady_relative_l2=1e-6,
        sample_count_u=16,
        sample_count_v=17,
    ) == ["exact_sample_count_failed"]


def test_validation_v2_observed_order_requires_positive_p_and_quality_gates(tmp_path):
    module = load_module(ROOT / "scripts/run_cavity_validation_v2.py")
    run_root = tmp_path / "v2"
    for resolution, offset in [(20, 0.0), (40, 0.5), (80, 0.75), (160, 0.875)]:
        results = run_root / f"N{resolution}" / "results"
        results.mkdir(parents=True)
        rows = [(float(i), offset) for i in range(17)]
        module.write_profile_with_actual_coordinates(
            results / "centerline_u_exact.csv",
            rows,
            coordinate_name="y",
            component_name="Ux",
            fixed_axis="x",
            fixed_value=0.5,
        )
        module.write_profile_with_actual_coordinates(
            results / "centerline_v_exact.csv",
            rows,
            coordinate_name="x",
            component_name="Uy",
            fixed_axis="y",
            fixed_value=0.5,
        )
    run_results = [
        module.RunResult(resolution, "completed", run_root / f"N{resolution}", 20.0, True)
        for resolution in [20, 40, 80, 160]
    ]

    rows = module.build_self_convergence(run_results, run_root)

    assert rows[0]["d20_40"] > rows[0]["d40_80"] > rows[0]["d80_160"]
    assert rows[0]["p_20_40_80"] == 1.0
    assert rows[0]["p_40_80_160"] == 1.0
    assert rows[0]["self_convergence_established"] is True
    run_results_failed = [
        module.RunResult(resolution, "completed", run_root / f"N{resolution}", 20.0, resolution != 160)
        for resolution in [20, 40, 80, 160]
    ]
    rows_failed = module.build_self_convergence(run_results_failed, run_root)
    assert rows_failed[0]["self_convergence_established"] is False


def test_validation_v2_manifest_keeps_failed_or_not_converged_rows(tmp_path):
    module = load_module(ROOT / "scripts/run_cavity_validation_v2.py")
    run_root = tmp_path / "v2"
    run_results = [
        module.RunResult(20, "completed", run_root / "N20", 20.0, True),
        module.RunResult(40, "not_converged", run_root / "N40", 80.0, False),
    ]

    module.write_manifest(run_results, run_root)

    rows = list(csv.DictReader((run_root / "manifest.csv").open()))
    assert rows == [
        {"resolution": "20", "status": "completed", "output_root": str(run_root / "N20")},
        {"resolution": "40", "status": "not_converged", "output_root": str(run_root / "N40")},
    ]


def test_export_validation_v2_public_copies_files_and_generates_readme_from_csv(tmp_path):
    module = load_module(ROOT / "scripts/export_validation_v2_public.py")
    run_root = tmp_path / "runs" / "cavity_validation_v2"
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
        "160,completed,True,True,40,0.0025,0.0025,0.0025,0.39,4e-6,True,0.004,0.006,0.006,0.010,17,17\n"
    )
    (run_root / "self_convergence.csv").write_text(
        "profile,d20_40,d40_80,d80_160,p_20_40_80,p_40_80_160,differences_decrease,"
        "quality_gates_passed,self_convergence_established,observed_order_status\n"
        "u,0.004,0.001,0.0003,1.9,2.0,True,True,True,established\n"
        "v,0.006,0.002,0.0005,1.8,2.1,True,True,True,established\n"
    )
    (run_root / "quality_gates.csv").write_text(
        "resolution,status,courant_gate_passed,steady_gate_passed,exact_sample_gate_passed,"
        "quality_gate_passed,material_centerline_change,quality_gate_reasons\n"
        "20,completed,True,True,True,True,1e-6,\n"
        "40,completed,True,True,True,True,2e-6,\n"
        "80,completed,True,True,True,True,3e-6,\n"
        "160,completed,True,True,True,True,4e-6,\n"
    )
    (run_root / "manifest.csv").write_text(
        "resolution,status,output_root\n"
        "20,completed,/local/private/path/N20\n"
        "40,completed,/local/private/path/N40\n"
        "80,completed,/local/private/path/N80\n"
        "160,completed,/local/private/path/N160\n"
    )
    for name in module.FIGURE_EXPORTS:
        (figures / name).write_bytes(b"png")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Old Title\n\n"
        "Old opening text.\n\n"
        "## What This Project Demonstrates\n\n"
        "Body\n\n"
        "<!-- cavity-validation-v2:start -->\n"
        "old generated text\n"
        "<!-- cavity-validation-v2:end -->\n"
        "## Governing Equations\n\n"
        "Equation body.\n\n"
        "## Physical Setup\n\n"
        "Old physical setup.\n\n"
        "## Mesh\n\n"
        "Old mesh text.\n\n"
        "## Boundary Conditions\n\n"
        "Boundary body.\n\n"
        "## Reproduction\n\n"
        "Old reproduction text.\n\n"
        "## Cloud Reproduction with GitHub Actions\n\n"
        "Cloud body.\n\n"
        "## Limitations\n\n"
        "Old limitations.\n",
        encoding="utf-8",
    )

    module.export_validation_v2(
        run_root,
        tmp_path / "results/public/cavity_validation_v2",
        tmp_path / "figures/cavity_validation_v2",
        readme,
    )

    readme_text = readme.read_text(encoding="utf-8")
    assert readme_text.startswith(
        "# OpenFOAM CFD Validation Lab: Laminar Cavity Verification and Paired RANS Diagnostics"
    )
    assert "Validation V2 Results" in readme_text
    assert "two reproducible OpenFOAM CFD studies" in readme_text
    assert "numerical validation and paired model diagnostics" in readme_text
    assert "four-grid OpenFOAM-10 validation workflow" in readme_text
    assert "exact 17-point centerline sampling" in readme_text
    assert "paired `kEpsilon` / `kOmegaSST` diagnostic" in readme_text
    assert "public continuation snapshot is `1098 / 1802` iterations" in readme_text
    assert "not a turbulence-model accuracy ranking" in readme_text
    assert "OpenFOAM-10" in readme_text
    assert "GitHub Actions OpenFOAM-11 workflow is retained as a smoke reproduction path" in readme_text
    assert "`runs/` contains the untracked full local solver fields and logs for validation-v2" in readme_text
    assert "`results/public/` and `figures/` contain the lightweight public CSV summaries and figures exported from those runs" in readme_text
    assert "centerline self-convergence" in readme_text
    assert "not on the full two-dimensional velocity field" in readme_text
    assert "All four grids pass the Courant, fixed-5 steady-state, solver-integrity, and exact-sampling quality gates." in readme_text
    assert "centerline grid-to-grid differences continuously decrease" in readme_text
    assert "observed centerline self-convergence order ranges from approximately `1.80` to `2.10`" in readme_text
    assert "Ghia pointwise RMSE values are not strictly monotonic" in readme_text
    assert "All four grid quality gates passed" not in readme_text
    assert "Ghia-reference RMSE values are not strictly monotonic" not in readme_text
    assert "Smoke reproduction mesh: `40 x 40 x 1`" in readme_text
    assert "Validation-v2 meshes: `20 x 20 x 1`, `40 x 40 x 1`, `80 x 80 x 1`, and `160 x 160 x 1`" in readme_text
    assert "python scripts/run_cavity_validation_v2.py --overwrite --resolutions 20 40 80 160 --end-times 20 30 40 50 60 70 80" in readme_text
    assert "python scripts/export_validation_v2_public.py" in readme_text
    assert "`runs/cavity_validation_v2/` is not tracked by git" in readme_text
    assert "constant/physicalProperties` and `constant/transportProperties`" in readme_text
    assert "The actual OpenFOAM version used for each validation run is recorded in run metadata" in readme_text
    assert "Reference Data" in readme_text
    assert "Ghia, Ghia & Shin (1982)" in readme_text
    assert "Journal of Computational Physics 48(3), 387-411" in readme_text
    assert "10.1016/0021-9991(82)90058-4" in readme_text
    assert "data/reference/source.json" in readme_text
    assert "not the full paper text" in readme_text
    assert "The validation scope is limited to the two-dimensional laminar `Re = 100` lid-driven cavity and 17 centerline sample points per profile." in readme_text
    assert "should not be extrapolated to turbulence, complex geometries, industrial meshes, or production CFD workflows" in readme_text
    assert "The OpenFOAM-11 GitHub Actions workflow is a smoke reproduction for the base case; the full validation-v2 evidence comes from local OpenFOAM-10 runs." in readme_text
    assert "not benchmark-grade validation" not in readme_text
    assert "0.005000" not in readme_text
    assert (tmp_path / "results/public/cavity_validation_v2/validation_summary.csv").is_file()
    assert (tmp_path / "figures/cavity_validation_v2/exact_centerline_vs_ghia.png").is_file()
    exported_manifest = (tmp_path / "results/public/cavity_validation_v2/manifest.csv").read_text()
    assert exported_manifest == (
        "resolution,status,run_id\n"
        "20,completed,N20\n"
        "40,completed,N40\n"
        "80,completed,N80\n"
        "160,completed,N160\n"
    )
    module.export_validation_v2(
        run_root,
        tmp_path / "results/public/cavity_validation_v2",
        tmp_path / "figures/cavity_validation_v2",
        readme,
    )
    assert readme.read_text(encoding="utf-8") == readme_text
