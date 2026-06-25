# Method Notes

These notes describe the OpenFOAM workflow used for the Reynolds-number-100 lid-driven cavity case. The repository contains a base smoke reproduction path and a separate validation-v2 export based on four OpenFOAM-10 grid runs.

## Workflow Overview

The base workflow is:

1. Select or generate a structured cavity mesh dictionary.
2. Generate the mesh with `blockMesh`.
3. Inspect mesh quality with `checkMesh`.
4. Run the transient incompressible solver with `icoFoam`.
5. Parse residual histories from the solver log.
6. Post-process final-time velocity fields with Python.
7. Write centerline CSV files and figures.

The validation-v2 workflow extends this by running `N20`, `N40`, `N80`, and `N160` grids, sampling the Ghia centerline coordinates with OpenFOAM `postProcess`, computing centerline metrics, and exporting lightweight public summaries.

## Mesh Generation

`system/blockMeshDict` defines a thin 3D slab representation of the unit-square cavity. The front and back patches use the OpenFOAM `empty` type so the simulation is effectively two-dimensional.

The base smoke reproduction uses the tracked `40 x 40 x 1` dictionary by default. The validation-v2 workflow generates `20 x 20 x 1`, `40 x 40 x 1`, `80 x 80 x 1`, and `160 x 160 x 1` dictionaries for the grid sequence.

## Boundary and Initial Fields

The `0/U` file initializes the velocity field and defines the moving lid:

- `top`: moving wall with `U = (1 0 0)`.
- `bottom`, `left`, `right`: no-slip walls.
- `frontAndBack`: `empty`.

The `0/p` file defines kinematic pressure with `zeroGradient` wall boundaries and `empty` front/back boundaries.

## Physical Properties

The case includes both `constant/physicalProperties` and `constant/transportProperties` so the viscosity is explicit across common OpenFOAM variants. Both files define:

```text
nu [0 2 -1 0 0 0 0] 0.01
```

With `U = 1` and `L = 1`, this gives:

```text
Re = U L / nu = 100
```

`constant/transportProperties` is retained for compatibility with OpenFOAM variants that still read it.

The actual OpenFOAM version used for each validation run is recorded in run metadata. The GitHub Actions smoke workflow records its OpenFOAM container image in `.github/workflows/reproduce.yml`.

## Solver Configuration

The base smoke case uses `icoFoam` with the tracked `system/controlDict`, `system/fvSchemes`, and `system/fvSolution` dictionaries. `fvSolution` sets the pressure reference:

```text
pRefCell 0
pRefValue 0
```

Validation-v2 uses fixed time stepping and records per-grid solver metadata, Courant diagnostics, steady-state checks over the final five physical time units, exact sample counts, and final-time centerline profiles.

## Residual Parsing

`scripts/run_cavity.sh` writes solver output to `results/logs/icoFoam.log` for the base smoke case. `scripts/plot_residuals.py` parses `Initial residual` entries from that log and writes:

- `results/residuals.csv`
- `figures/cavity_residuals.png`

Validation-v2 stores per-grid solver logs and summary tables under the local run directory before exporting public CSV summaries.

## Centerline Extraction

For the base smoke case, `scripts/postprocess_cavity.py` reads final-time OpenFOAM velocity fields. If the `C` cell-center field is available from `writeCellCentres`, it uses that field. Otherwise, it reconstructs structured cell centers from `system/blockMeshDict`.

The smoke-run centerline CSVs are computed from final-time OpenFOAM field output:

- `results/centerline_u.csv`: nearest-cell values to `x = 0.5`, written as `y,Ux`.
- `results/centerline_v.csv`: nearest-cell values to `y = 0.5`, written as `x,Uy`.

For validation-v2, OpenFOAM `postProcess` point sampling with `cellPoint` interpolation is used at the 17 Ghia centerline coordinates for each profile. The validation claim is limited to centerline self-convergence for those sampled profiles.

## Reference Data

The validation-v2 centerline comparisons use the Re=100 reference data from Ghia, Ghia & Shin (1982):

U. Ghia, K. N. Ghia, and C. T. Shin, "High-Re Solutions for Incompressible Flow Using the Navier-Stokes Equations and a Multigrid Method," Journal of Computational Physics 48(3), 387-411. DOI: `10.1016/0021-9991(82)90058-4`.

Reference metadata, source links, and checksums are recorded in `data/reference/source.json`. The repository stores lightweight tabulated reference values and metadata, not the full paper text.

## Field Visualization

`scripts/postprocess_cavity.py` generates `figures/cavity_velocity_magnitude.png` from final-time cell-centered velocity values.

`foamToVTK` is used as a best-effort optional export path for local field inspection. ParaView is recommended for richer visualization of OpenFOAM fields.

## Reproducibility Notes

- Generated mesh and time directories are ignored by git because they can be regenerated.
- The required base smoke logs, CSV summaries, and figures are checked by `scripts/check_outputs.py`.
- The GitHub Actions workflow runs the base smoke solver stage in an OpenFOAM 11 Docker image and performs Python post-processing on the runner.
- `runs/` contains untracked full local solver fields and logs for validation-v2.
- The public validation-v2 summaries are exported to `results/public/cavity_validation_v2/` and `figures/cavity_validation_v2/`.
- The validation scope is limited to the two-dimensional laminar `Re = 100` cavity and 17 centerline sample points per profile; it should not be extrapolated to turbulence, complex geometries, industrial meshes, or production CFD workflows.
- The OpenFOAM-11 GitHub Actions workflow is a smoke reproduction for the base case; the full validation-v2 evidence comes from local OpenFOAM-10 runs.
