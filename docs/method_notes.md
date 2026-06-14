# Method Notes

This mini-lab is set up to show a conventional OpenFOAM workflow for a laminar incompressible lid-driven cavity. The files in `cases/lid_driven_cavity/` define the setup. Solver-derived outputs are generated only by running the scripts in an OpenFOAM-enabled shell.

## Workflow

1. `blockMesh`

   Reads `system/blockMeshDict` and creates the structured hexahedral mesh under `constant/polyMesh/`. The repository includes `system/blockMeshDict.20x20` and `system/blockMeshDict.40x40`; `scripts/run_cavity.sh` copies the selected dictionary to `system/blockMeshDict` before running `blockMesh`. Both meshes use a thin 3D slab with `empty` front/back patches, making the calculation effectively 2D.

2. Boundary and initial fields

   The `0/U` file sets an initially stationary velocity field, a moving top lid with `U = (1 0 0)`, no-slip side/bottom walls, and `empty` front/back patches. The `0/p` file sets kinematic pressure with `zeroGradient` wall boundaries.

3. Physical properties

   `constant/transportProperties` defines Newtonian laminar flow with `nu = 0.01`. With `U = 1` and `L = 1`, this gives `Re = 100`.

4. `fvSchemes`

   `system/fvSchemes` selects finite-volume discretization schemes: Euler time stepping, linear interpolation, Gauss linear gradients, and corrected Laplacians/snGrad terms.

5. `fvSolution`

   `system/fvSolution` defines linear solvers and PISO controls for `icoFoam`. It also supplies a pressure reference through `pRefCell 0` and `pRefValue 0`.

6. `controlDict`

   `system/controlDict` selects `icoFoam`, sets `deltaT = 0.005`, runs to `endTime = 5`, writes every 100 time steps, and keeps only the two latest time directories via `purgeWrite 2`.

7. Residual logs

   `scripts/run_cavity.sh` redirects solver output to `results/logs/icoFoam.log`. `scripts/plot_residuals.py` parses `Initial residual` lines from that log and can write `results/residuals.csv` plus `figures/cavity_residuals.png`.

8. Sampling

   `system/sampleDict` defines two uniform sampled sets:

   - vertical centerline: `x = 0.5`, from `y = 0` to `y = 1`
   - horizontal centerline: `y = 0.5`, from `x = 0` to `x = 1`

   `scripts/postprocess_cavity.py` reads real OpenFOAM sample outputs and writes `results/centerline_u.csv`, `results/centerline_v.csv`, and `figures/cavity_centerline_profiles.png`.

9. Field visualization

   If `foamToVTK` is available, `scripts/run_cavity.sh` exports the latest velocity field. `scripts/postprocess_cavity.py` can read a legacy VTK file containing `VECTORS U` and generate `figures/cavity_velocity_magnitude.png`. If VTK export is unavailable, use ParaView locally after running the case.

## Reproducibility Notes

- Generated OpenFOAM mesh/time directories are ignored by git.
- Results and figures should be committed only when they come from an actual solver run.
- The current repository setup has tests for required files, Re=100 parameters, residual parsing, centerline CSV writing, and conservative CV bullets.
