# Method Notes

This mini-lab documents a conventional OpenFOAM workflow for a laminar incompressible lid-driven cavity at `Re = 100`. The case files define the mesh, fields, physical properties, numerical schemes, solver controls, and post-processing workflow.

## Workflow Overview

The main workflow is:

1. Select a structured mesh dictionary.
2. Generate the mesh with `blockMesh`.
3. Inspect mesh quality with `checkMesh`.
4. Run the transient incompressible solver with `icoFoam`.
5. Parse residual histories from the solver log.
6. Post-process final-time velocity fields with Python.
7. Write centerline CSV files and figures.

## Mesh Generation

`system/blockMeshDict` defines a thin 3D slab representation of the unit-square cavity. The front and back patches use the OpenFOAM `empty` type so the simulation is effectively two-dimensional.

The repository includes two selectable mesh dictionaries:

- `system/blockMeshDict.20x20`
- `system/blockMeshDict.40x40`

`scripts/run_cavity.sh` copies the selected dictionary to `system/blockMeshDict` before running `blockMesh`.

## Boundary and Initial Fields

The `0/U` file initializes the velocity field and defines the moving lid:

- `top`: moving wall with `U = (1 0 0)`.
- `bottom`, `left`, `right`: no-slip walls.
- `frontAndBack`: `empty`.

The `0/p` file defines kinematic pressure with `zeroGradient` wall boundaries and `empty` front/back boundaries.

## Physical Properties

OpenFOAM Foundation v11 reads `constant/physicalProperties`, which defines:

```text
nu [0 2 -1 0 0 0 0] 0.01
```

With `U = 1` and `L = 1`, this gives:

```text
Re = U L / nu = 100
```

`constant/transportProperties` is retained for compatibility with OpenFOAM variants that still read it.

## Solver Configuration

`system/controlDict` selects `icoFoam`, sets `deltaT = 0.005`, runs to `endTime = 5`, writes every 100 time steps, and keeps only the two latest time directories.

`system/fvSchemes` specifies finite-volume discretization schemes for the transient laminar solve.

`system/fvSolution` defines linear solvers, PISO controls, and the pressure reference:

```text
pRefCell 0
pRefValue 0
```

## Residual Parsing

`scripts/run_cavity.sh` writes solver output to `results/logs/icoFoam.log`.

`scripts/plot_residuals.py` parses `Initial residual` entries from that log and writes:

- `results/residuals.csv`
- `figures/cavity_residuals.png`

## Centerline Extraction

`scripts/postprocess_cavity.py` reads final-time OpenFOAM velocity fields. If the `C` cell-center field is available from `writeCellCentres`, it uses that field. Otherwise, it reconstructs structured cell centers from `system/blockMeshDict`.

The centerline CSVs are computed from final-time OpenFOAM field output:

- `results/centerline_u.csv`: nearest-cell values to `x = 0.5`, written as `y,Ux`.
- `results/centerline_v.csv`: nearest-cell values to `y = 0.5`, written as `x,Uy`.

The profiles are diagnostic outputs for this workflow and are not a reference-data validation study.

## Field Visualization

`scripts/postprocess_cavity.py` generates `figures/cavity_velocity_magnitude.png` from final-time cell-centered velocity values.

`foamToVTK` is used as a best-effort optional export path for local field inspection. ParaView is recommended for richer visualization of OpenFOAM fields.

## Reproducibility Notes

- Generated mesh and time directories are ignored by git because they can be regenerated.
- The required solver logs, CSV summaries, and figures are checked by `scripts/check_outputs.py`.
- The GitHub Actions workflow runs the OpenFOAM solver stage in a Docker image and performs Python post-processing on the runner.
