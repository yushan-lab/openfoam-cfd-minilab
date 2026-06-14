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
        "Makefile",
        "cases/lid_driven_cavity/0/U",
        "cases/lid_driven_cavity/0/p",
        "cases/lid_driven_cavity/constant/physicalProperties",
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


def test_clean_public_repository_does_not_include_agents_md():
    assert not (ROOT / "AGENTS.md").exists()


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


def test_openfoam11_physical_properties_file_declares_re100_viscosity():
    physical_path = ROOT / "cases/lid_driven_cavity/constant/physicalProperties"

    assert physical_path.exists()

    physical = physical_path.read_text()

    assert "object      physicalProperties;" in physical
    assert "nu              [0 2 -1 0 0 0 0] 0.01;" in physical


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
            "results/residuals.csv",
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
    assert "set +e\nMESH_RESOLUTION=40 RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh\nRUN_STATUS=$?\nset -e" in script
    assert 'echo "run_cavity.sh exit status: $RUN_STATUS"' in script
    assert "results/logs/writeCellCentres.log" in script
    assert "results/logs/writeCellCentres_skipped.log" in script
    assert "results/logs/postProcess_sample.log" not in script
    assert "results/logs/foamToVTK.log" in script
    assert "results/logs/foamToVTK_skipped.log" in script
    assert "results/logs/python_postprocess_skipped.log" in script
    assert 'echo "===== tail: $f ====="' in script
    assert 'tail -n 80 "$f" || true' in script
    assert 'echo "Missing log: $f"' in script
    assert 'if [ "$RUN_STATUS" -ne 0 ]; then' in script
    assert 'echo "run_cavity.sh failed; see log tails above." >&2' in script
    assert 'exit "$RUN_STATUS"' in script
    assert "MESH_RESOLUTION=40 RUN_PYTHON_POSTPROCESS=0 bash scripts/run_cavity.sh" in script
    assert "tail -n 30 results/logs/blockMesh.log" not in script
    assert "tail -n 30 results/logs/checkMesh.log" not in script
    assert "tail -n 30 results/logs/icoFoam.log" not in script
    assert 'grep -q "End" results/logs/icoFoam.log' in script
    assert script.index('if [ "$RUN_STATUS" -ne 0 ]; then') < script.index("for log_file in")
    assert script.index('grep -q "End" results/logs/icoFoam.log') > script.index("for log_file in")
    assert script.index("for log_file in") < script.index('echo "END_OPENFOAM_RUN"')
    assert "find results figures -maxdepth 4 -type f | sort" in script
    assert 'echo "END_OPENFOAM_RUN"' in script


def test_run_script_requires_solver_only_and_treats_openfoam_postprocessing_as_optional():
    script = (ROOT / "scripts/run_cavity.sh").read_text()

    assert 'LOG_DIR="$ROOT_DIR/results/logs"' in script
    assert 'RESULT_DIR="$ROOT_DIR/results"' in script
    assert 'FIGURE_DIR="$ROOT_DIR/figures"' in script
    assert 'mkdir -p "$LOG_DIR" "$FIGURE_DIR" "$RESULT_DIR"' in script
    assert "for cmd in blockMesh checkMesh icoFoam; do" in script
    assert "for cmd in blockMesh checkMesh icoFoam postProcess; do" not in script
    assert 'blockMesh -case "$CASE_DIR"' in script
    assert 'checkMesh -case "$CASE_DIR"' in script
    assert 'icoFoam -case "$CASE_DIR"' in script
    assert "verify_solver_logs" in script
    assert 'verify_nonempty_file "$LOG_DIR/blockMesh.log"' in script
    assert 'verify_nonempty_file "$LOG_DIR/checkMesh.log"' in script
    assert 'verify_nonempty_file "$LOG_DIR/icoFoam.log"' in script
    assert 'postProcess -case "$CASE_DIR" -func writeCellCentres -latestTime' in script
    assert "writeCellCentres_skipped.log" in script
    assert "postProcess -func sample" not in script
    assert "sample -latestTime" not in script
    assert "postProcess_sample.log" not in script
    assert "foamToVTK_skipped.log" in script
    assert 'if ! foamToVTK -case "$CASE_DIR" -latestTime -fields "(U)"' in script
    assert 'find "$ROOT_DIR/results" "$ROOT_DIR/figures" -maxdepth 3 -type f | sort' in script


def test_postprocess_reconstructs_structured_centres_from_case_block_mesh():
    module = load_module(ROOT / "scripts/postprocess_cavity.py")

    centres = module.reconstruct_structured_cell_centres(
        ROOT / "cases/lid_driven_cavity/system/blockMeshDict"
    )

    assert len(centres) == 40 * 40
    assert centres[0] == (0.0125, 0.0125, 0.0)
    assert centres[-1] == (0.9875, 0.9875, 0.0)


def test_postprocess_reads_final_time_u_and_extracts_centerlines_without_sample_output():
    module = load_module(ROOT / "scripts/postprocess_cavity.py")

    with TemporaryDirectory() as temp_dir:
        case_dir = Path(temp_dir) / "case"
        (case_dir / "system").mkdir(parents=True)
        (case_dir / "5").mkdir()
        (case_dir / "system/blockMeshDict").write_text(
            """
            FoamFile { object blockMeshDict; }
            vertices
            (
                (0 0 -0.005)
                (1 0 -0.005)
                (1 1 -0.005)
                (0 1 -0.005)
                (0 0  0.005)
                (1 0  0.005)
                (1 1  0.005)
                (0 1  0.005)
            );
            blocks
            (
                hex (0 1 2 3 4 5 6 7) (2 2 1) simpleGrading (1 1 1)
            );
            """
        )
        (case_dir / "5/U").write_text(
            """
            FoamFile { object U; }
            dimensions [0 1 -1 0 0 0 0];
            internalField nonuniform List<vector>
            4
            (
            (0.10 -0.10 0)
            (0.20 -0.20 0)
            (0.30 -0.30 0)
            (0.40 -0.40 0)
            )
            ;
            boundaryField {}
            """
        )

        cells, latest_time, centre_source = module.load_final_velocity_cells(case_dir)
        vertical_rows = module.extract_nearest_centerline(
            cells, fixed_axis="x", fixed_value=0.5, varying_axis="y"
        )
        horizontal_rows = module.extract_nearest_centerline(
            cells, fixed_axis="y", fixed_value=0.5, varying_axis="x"
        )

        assert latest_time == "5"
        assert centre_source == "reconstructed from blockMeshDict"
        assert [(row.y, row.Ux) for row in vertical_rows] == [(0.25, 0.1), (0.75, 0.3)]
        assert [(row.x, row.Uy) for row in horizontal_rows] == [(0.25, -0.1), (0.75, -0.2)]


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


def test_docs_describe_cloud_reproduction_and_clean_resume_content():
    readme = (ROOT / "README.md").read_text()
    cv_text = (ROOT / "docs/cv_bullets.md").read_text()

    assert "Cloud reproduction with GitHub Actions" in readme
    assert "OpenFOAM Docker image" in readme
    assert "OpenFOAM 11" in readme
    assert "physicalProperties" in readme
    assert "blockMesh/checkMesh/icoFoam" in readme
    assert "final-time OpenFOAM field output" in readme
    assert "transportProperties is the only" not in readme
    assert "only required viscosity file" not in readme.lower()
    assert "Run postProcess -func sample" not in readme
    assert "centerline profiles are generated only from OpenFOAM sample files" not in readme
    assert "OpenFOAM 11 mini-project" in cv_text
    assert "Re=100 lid-driven cavity" in cv_text
    assert "GitHub Actions reproduction workflow" in cv_text
    assert "icoFoam" in cv_text
    assert "nearest-cell centerline velocity profiles" in cv_text


def test_public_markdown_files_do_not_contain_internal_terms():
    public_markdown_paths = [
        ROOT / "README.md",
        ROOT / "docs/cv_bullets.md",
        ROOT / "docs/method_notes.md",
    ]
    banned_terms = [
        "Codex",
        "AI-generated",
        "AI-assisted",
        "fabricated",
        "not fabricated",
        "downloaded artifact",
        "copied artifact",
        "artifact-backed",
        "artifact evidence",
        "current local artifact",
        "workflow_debug",
        "2026-06-14",
        "Before successful",
        "After successful",
        "scaffold",
        "local working tree",
    ]

    violations = []
    for path in public_markdown_paths:
        text = path.read_text().lower()
        for term in banned_terms:
            if term.lower() in text:
                violations.append(f"{path.relative_to(ROOT)} contains {term!r}")

    assert violations == []


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
