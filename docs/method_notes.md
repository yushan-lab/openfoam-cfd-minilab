# Method Notes

This mini-lab is set up to show a conventional OpenFOAM workflow for a laminar incompressible lid-driven cavity. The files in `cases/lid_driven_cavity/` define the setup. Solver-derived outputs are generated only by running the scripts in an OpenFOAM-enabled shell.

## Workflow

1. `blockMesh`

   Reads `system/blockMeshDict` and creates the structured hexahedral mesh under `constant/polyMesh/`. The repository includes `system/blockMeshDict.20x20` and `system/blockMeshDict.40x40`; `scripts/run_cavity.sh` copies the selected dictionary to `system/blockMeshDict` before running `blockMesh`. Both meshes use a thin 3D slab with `empty` front/back patches, making the calculation effectively 2D.

2. Boundary and initial fields

   The `0/U` file sets an initially stationary velocity field, a moving top lid with `U = (1 0 0)`, no-slip side/bottom walls, and `empty` front/back patches. The `0/p` file sets kinematic pressure with `zeroGradient` wall boundaries.

3. Physical properties

   `constant/physicalProperties` defines the OpenFOAM Foundation v11 kinematic viscosity with `nu = 0.01`. The older `constant/transportProperties` file is retained for compatibility with variants that still read it. With `U = 1` and `L = 1`, this gives `Re = 100`.

4. `fvSchemes`

   `system/fvSchemes` selects finite-volume discretization schemes: Euler time stepping, linear interpolation, Gauss linear gradients, and corrected Laplacians/snGrad terms.

5. `fvSolution`

   `system/fvSolution` defines linear solvers and PISO controls for `icoFoam`. It also supplies a pressure reference through `pRefCell 0` and `pRefValue 0`.

6. `controlDict`

   `system/controlDict` selects `icoFoam`, sets `deltaT = 0.005`, runs to `endTime = 5`, writes every 100 time steps, and keeps only the two latest time directories via `purgeWrite 2`.

7. Residual logs

   `scripts/run_cavity.sh` redirects solver output to `results/logs/icoFoam.log`. `scripts/plot_residuals.py` parses `Initial residual` lines from that log and can write `results/residuals.csv` plus `figures/cavity_residuals.png`.

8. Centerline extraction

   OpenFOAM Foundation v11 in the CI container does not require the legacy `sample` command for this project. After `icoFoam`, Python post-processing reads the final-time OpenFOAM `U` volVectorField directly. If the best-effort `writeCellCentres` function creates a final-time `C` field, the Python script uses those cell centres. Otherwise, it reconstructs the structured cell centres from `system/blockMeshDict`.

   The centerline CSVs use nearest-cell extraction:

   - vertical profile: nearest cell values to `x = 0.5`, written as `y,Ux`
   - horizontal profile: nearest cell values to `y = 0.5`, written as `x,Uy`

   This produces `results/centerline_u.csv`, `results/centerline_v.csv`, and `figures/cavity_centerline_profiles.png` from final-time OpenFOAM field output without fabricating or hard-coding profile values.

9. Field visualization

   `scripts/postprocess_cavity.py` also uses the final-time cell-centred `U` values to generate `figures/cavity_velocity_magnitude.png`. `foamToVTK` remains a best-effort optional export for local inspection, and ParaView remains the recommended tool for detailed visual review.

## Reproducibility Notes

- Generated OpenFOAM mesh/time directories are ignored by git.
- Results and figures should be committed only when they come from an actual solver run.
- The current repository setup has tests for required files, Re=100 parameters, residual parsing, final-field centerline extraction, output checking, and conservative CV bullets.
