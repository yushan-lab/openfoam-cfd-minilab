from pathlib import Path
import importlib.util
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


def test_required_repository_layout_exists():
    required_paths = [
        "README.md",
        "AGENTS.md",
        "Makefile",
        "cases/lid_driven_cavity/0/U",
        "cases/lid_driven_cavity/0/p",
        "cases/lid_driven_cavity/constant/transportProperties",
        "cases/lid_driven_cavity/system/blockMeshDict",
        "cases/lid_driven_cavity/system/blockMeshDict.20x20",
        "cases/lid_driven_cavity/system/blockMeshDict.40x40",
        "cases/lid_driven_cavity/system/controlDict",
        "cases/lid_driven_cavity/system/fvSchemes",
        "cases/lid_driven_cavity/system/fvSolution",
        "cases/lid_driven_cavity/system/sampleDict",
        "scripts/run_cavity.sh",
        "scripts/clean_case.sh",
        "scripts/plot_residuals.py",
        "scripts/postprocess_cavity.py",
        "scripts/check_outputs.py",
        "scripts/ci_run_openfoam_in_container.sh",
        ".github/workflows/reproduce.yml",
        "results/.gitkeep",
        "figures/.gitkeep",
        "docs/method_notes.md",
        "docs/cv_bullets.md",
    ]

    missing = [path for path in required_paths if not (ROOT / path).exists()]

    assert missing == []


def test_case_files_encode_re100_lid_driven_cavity_setup():
    transport = (ROOT / "cases/lid_driven_cavity/constant/transportProperties").read_text()
    velocity = (ROOT / "cases/lid_driven_cavity/0/U").read_text()
    pressure = (ROOT / "cases/lid_driven_cavity/0/p").read_text()
    mesh = (ROOT / "cases/lid_driven_cavity/system/blockMeshDict").read_text()
    mesh_20 = (ROOT / "cases/lid_driven_cavity/system/blockMeshDict.20x20").read_text()
    mesh_40 = (ROOT / "cases/lid_driven_cavity/system/blockMeshDict.40x40").read_text()

    assert "nu              [0 2 -1 0 0 0 0] 0.01;" in transport
    assert "movingWallVelocity" in velocity
    assert "value           uniform (1 0 0);" in velocity
    assert velocity.count("noSlip") >= 3
    assert pressure.count("zeroGradient") >= 4
    assert "(40 40 1)" in mesh
    assert "(20 20 1)" in mesh_20
    assert "(40 40 1)" in mesh_40
    assert "frontAndBack" in mesh
    assert "type empty;" in mesh


def test_plot_residuals_parser_extracts_icofoam_initial_residuals():
    module = load_module(ROOT / "scripts/plot_residuals.py")
    log_text = """
Time = 0.005
smoothSolver:  Solving for Ux, Initial residual = 0.12, Final residual = 8e-06, No Iterations 1
smoothSolver:  Solving for Uy, Initial residual = 0.03, Final residual = 7e-06, No Iterations 1
DICPCG:  Solving for p, Initial residual = 0.2, Final residual = 9e-07, No Iterations 12
Time = 0.010
smoothSolver:  Solving for Ux, Initial residual = 0.09, Final residual = 6e-06, No Iterations 1
smoothSolver:  Solving for Uy, Initial residual = 0.02, Final residual = 5e-06, No Iterations 1
DICPCG:  Solving for p, Initial residual = 0.15, Final residual = 8e-07, No Iterations 10
"""

    series = module.parse_residuals(log_text)

    assert series["Ux"] == [(0.005, 0.12), (0.01, 0.09)]
    assert series["Uy"] == [(0.005, 0.03), (0.01, 0.02)]
    assert series["p"] == [(0.005, 0.2), (0.01, 0.15)]


def test_postprocess_sample_parser_writes_centerline_csvs():
    module = load_module(ROOT / "scripts/postprocess_cavity.py")
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        sample_file = temp_path / "line_U.xy"
        sample_file.write_text(
            "# x y z Ux Uy Uz\n"
            "0.5 0.0 0.0 0.00 0.00 0.0\n"
            "0.5 0.5 0.0 0.20 -0.10 0.0\n"
            "0.5 1.0 0.0 1.00 0.00 0.0\n"
        )

        rows = module.read_vector_sample(sample_file)
        output_csv = temp_path / "centerline_u.csv"
        module.write_centerline_csv(output_csv, rows, coordinate="y", component="Ux")

        assert output_csv.read_text().splitlines() == [
            "y,Ux",
            "0.0,0.0",
            "0.5,0.2",
            "1.0,1.0",
        ]


def test_postprocess_vtk_parser_handles_cell_data_velocity():
    module = load_module(ROOT / "scripts/postprocess_cavity.py")
    with TemporaryDirectory() as temp_dir:
        vtk_file = Path(temp_dir) / "cavity.vtk"
        vtk_file.write_text(
            "# vtk DataFile Version 2.0\n"
            "cavity\n"
            "ASCII\n"
            "DATASET UNSTRUCTURED_GRID\n"
            "POINTS 4 float\n"
            "0 0 0\n"
            "1 0 0\n"
            "1 1 0\n"
            "0 1 0\n"
            "CELLS 1 5\n"
            "4 0 1 2 3\n"
            "CELL_TYPES 1\n"
            "9\n"
            "CELL_DATA 1\n"
            "VECTORS U float\n"
            "3 4 0\n"
        )

        points, magnitudes = module.read_legacy_vtk_velocity(vtk_file)

        assert points == [(0.5, 0.5)]
        assert magnitudes == [5.0]


def test_output_checker_requires_real_nonempty_outputs():
    module = load_module(ROOT / "scripts/check_outputs.py")

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        required = [
            "results/logs/blockMesh.log",
            "results/logs/checkMesh.log",
            "results/logs/icoFoam.log",
            "results/centerline_u.csv",
            "results/centerline_v.csv",
            "figures/cavity_residuals.png",
            "figures/cavity_centerline_profiles.png",
        ]
        for relative_path in required:
            path = temp_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("real output placeholder from test\n")

        report = module.check_outputs(temp_path)
        assert report.missing_required == []
        assert report.empty_required == []
        assert report.ok

        (temp_path / "results/centerline_v.csv").write_text("")
        report = module.check_outputs(temp_path)
        assert not report.ok
        assert "results/centerline_v.csv" in report.empty_required


def test_github_actions_reproduction_workflow_contract():
    workflow = (ROOT / ".github/workflows/reproduce.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "actions/checkout@v4" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "Prepare diagnostic output directories" in workflow
    assert "results/logs/workflow_debug.log" in workflow
    assert "openfoam/openfoam11-paraview510:11" in workflow
    assert "--entrypoint /bin/bash" in workflow
    assert 'bash scripts/ci_run_openfoam_in_container.sh' in workflow
    assert '--user "$(id -u):$(id -g)"' in workflow
    assert "-e HOME=/tmp" in workflow
    assert "chmod -R u+rwX results figures cases scripts" in workflow
    assert "set -o pipefail" in workflow
    assert "set -euo pipefail\n          docker run" not in workflow
    assert 'grep -q "BEGIN_OPENFOAM_RUN" results/logs/docker_openfoam.log' in workflow
    assert 'grep -q "END_OPENFOAM_RUN" results/logs/docker_openfoam.log' in workflow
    assert "test -s results/logs/blockMesh.log" in workflow
    assert "test -s results/logs/checkMesh.log" in workflow
    assert "test -s results/logs/icoFoam.log" in workflow
    assert "python -m pytest tests/test_cavity_repository.py -q --basetemp .pytest_tmp" in workflow
    assert "bash -n scripts/run_cavity.sh" in workflow
    assert "bash -n scripts/ci_run_openfoam_in_container.sh" in workflow
    assert "RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh" not in workflow
    assert "Diagnose files after Docker run" in workflow
    assert "if: always()" in workflow
    assert "pwd" in workflow
    assert "find . -maxdepth 6 -type f | sort" in workflow
    assert "ls -la results results/logs figures || true" in workflow
    assert "python scripts/plot_residuals.py" in workflow
    assert "python scripts/postprocess_cavity.py" in workflow
    assert "python scripts/check_outputs.py" in workflow
    assert "openfoam-cavity-results" in workflow


def test_ci_openfoam_container_script_contract():
    script = (ROOT / "scripts/ci_run_openfoam_in_container.sh").read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -eo pipefail" in script
    assert "set -euo pipefail" not in script
    assert 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in script
    assert 'mkdir -p "$ROOT_DIR/results/logs" "$ROOT_DIR/figures" "$ROOT_DIR/results"' in script
    assert 'echo "BEGIN_OPENFOAM_RUN"' in script
    assert 'echo "Container user:"' in script
    assert "\nid\n" in script
    assert 'ls -ld "$ROOT_DIR" "$ROOT_DIR/results" "$ROOT_DIR/results/logs" "$ROOT_DIR/cases" "$ROOT_DIR/figures"' in script
    assert 'touch "$ROOT_DIR/results/logs/write_test.log"' in script
    assert 'rm -f "$ROOT_DIR/results/logs/write_test.log"' in script
    assert "Container cannot write to results/logs; check Docker --user or workspace permissions." in script
    assert 'SOURCE_LOG="$ROOT_DIR/results/logs/source_openfoam.log"' in script
    assert script.index('touch "$ROOT_DIR/results/logs/write_test.log"') < script.index('SOURCE_LOG="$ROOT_DIR/results/logs/source_openfoam.log"')
    assert "set +e" in script
    assert "source /opt/openfoam11/etc/bashrc" in script
    assert "source /usr/lib/openfoam/openfoam11/etc/bashrc" in script
    assert 'echo "OpenFOAM source status: $SOURCE_STATUS"' in script
    assert 'cat "$SOURCE_LOG"' in script
    assert 'echo "WM_PROJECT_DIR=${WM_PROJECT_DIR:-unset}"' in script
    assert 'echo "PATH=$PATH"' in script
    assert 'echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-unset}"' in script
    assert "command -v blockMesh || true" in script
    assert "command -v checkMesh || true" in script
    assert "command -v icoFoam || true" in script
    assert "command -v postProcess || true" in script
    assert "OpenFOAM commands are not available after sourcing bashrc." in script
    assert "MESH_RESOLUTION=40 RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh" in script
    assert "tail -n 30 results/logs/blockMesh.log" in script
    assert "tail -n 30 results/logs/checkMesh.log" in script
    assert "tail -n 30 results/logs/icoFoam.log" in script
    assert "find results figures -maxdepth 4 -type f | sort" in script
    assert 'echo "END_OPENFOAM_RUN"' in script


def test_run_script_verifies_solver_logs_before_postprocessing():
    script = (ROOT / "scripts/run_cavity.sh").read_text()
    python_skip_index = script.index('if [ "$RUN_PYTHON_POSTPROCESS" = "0" ]')

    assert 'LOG_DIR="$ROOT_DIR/results/logs"' in script
    assert 'RESULT_DIR="$ROOT_DIR/results"' in script
    assert 'FIGURE_DIR="$ROOT_DIR/figures"' in script
    assert 'mkdir -p "$LOG_DIR" "$FIGURE_DIR" "$RESULT_DIR"' in script
    assert "verify_solver_logs" in script
    assert 'verify_nonempty_file "$LOG_DIR/blockMesh.log"' in script
    assert 'verify_nonempty_file "$LOG_DIR/checkMesh.log"' in script
    assert 'verify_nonempty_file "$LOG_DIR/icoFoam.log"' in script
    assert '\nverify_solver_logs\n\nif [ "$RUN_PYTHON_POSTPROCESS" = "0" ]' in script
    assert 'find "$ROOT_DIR/results" "$ROOT_DIR/figures" -maxdepth 3 -type f | sort' in script


def test_plot_residuals_reports_missing_log_with_directory_listing():
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        log_dir = temp_path / "results" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "blockMesh.log").write_text("block mesh log\n")
        missing_log = log_dir / "icoFoam.log"

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/plot_residuals.py"),
                "--log",
                str(missing_log),
                "--output",
                str(temp_path / "figures" / "cavity_residuals.png"),
            ],
            cwd=temp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        combined_output = completed.stdout + completed.stderr
        assert completed.returncode != 0
        assert f"Missing residual log: {missing_log}" in combined_output
        assert "Files currently under" in combined_output
        assert "blockMesh.log" in combined_output
        assert "Traceback" not in combined_output


def test_docs_describe_cloud_reproduction_and_cv_bullet_gating():
    readme = (ROOT / "README.md").read_text()
    cv_text = (ROOT / "docs/cv_bullets.md").read_text()

    assert "Cloud reproduction with GitHub Actions" in readme
    assert "OpenFOAM Docker image" in readme
    assert "openfoam-cavity-results" in readme
    assert "case setup" in readme.lower()
    assert "executed solver results" in readme.lower()
    assert "Before successful OpenFOAM reproduction" in cv_text
    assert "After successful OpenFOAM reproduction" in cv_text


def test_cv_bullets_do_not_claim_solver_execution_without_outputs():
    cv_text = (ROOT / "docs/cv_bullets.md").read_text().lower()
    forbidden_claims = [
        "successfully ran",
        "converged",
        "generated residual",
        "validated against",
        "computed drag",
    ]

    assert all(claim not in cv_text for claim in forbidden_claims)
