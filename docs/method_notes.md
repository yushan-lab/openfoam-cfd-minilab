# Method Notes

## Workflow Overview

The repository contains a compact OpenFOAM workflow for a Re=100 lid-driven cavity. The workflow generates the mesh, checks mesh quality, advances the transient incompressible case with `icoFoam`, parses solver residuals, extracts final-time centerline velocity profiles, and renders diagnostic figures.

## Mesh Generation

The case uses `blockMesh` with a structured unit-square cavity mesh. The default mesh is 40 x 40 x 1 cells, with an optional 20 x 20 x 1 mesh for quicker runs. The mesh dictionaries are stored as `blockMeshDict.40x40` and `blockMeshDict.20x20`; `scripts/run_cavity.sh` selects the requested dictionary and copies it into place before running `blockMesh`.

## Boundary and Initial Fields

The top boundary is a moving lid with velocity `U = (1, 0, 0)`. The bottom, left, and right boundaries are no-slip walls. The front and back patches are `empty`, giving an effective 2D setup through a thin z direction. Pressure uses wall `zeroGradient` conditions with reference pressure set through `pRefCell 0` and `pRefValue 0`.

## Physical Properties

The characteristic length is `L = 1`, the lid speed is `1`, and the kinematic viscosity is `nu = 0.01`, giving `Re = 100`. OpenFOAM Foundation v11 reads these properties from `constant/physicalProperties`; `constant/transportProperties` is also present for compatibility with OpenFOAM variants that still use that file.

## Solver Configuration

The case uses `icoFoam`, OpenFOAM's transient incompressible laminar solver. Time controls, discretization schemes, and linear-solver settings are defined through the case dictionaries in `system/`, including `controlDict`, `fvSchemes`, and `fvSolution`.

## Residual Parsing

Solver residual histories are parsed from `results/logs/icoFoam.log` into `results/residuals.csv`. The residual plot in `figures/cavity_residuals.png` provides a compact diagnostic view of solver convergence behavior during the transient run.

## Centerline Extraction

Final-time velocity fields are sampled from cell-centered OpenFOAM output. The post-processing step extracts nearest-cell values along the horizontal and vertical cavity centerlines, then writes `results/centerline_u.csv` and `results/centerline_v.csv`. These profiles are intended as workflow diagnostics rather than benchmark-grade validation.

## Field Visualization

The workflow creates diagnostic figures for residual histories, centerline velocity profiles, and final-time velocity magnitude. The velocity-magnitude visualization uses final-time field data exported from the OpenFOAM case.

## Reproducibility Notes

The GitHub Actions workflow runs the case with the OpenFOAM 11 Docker image, executes mesh generation, mesh checking, solving, post-processing, and validation, and uploads `results/` and `figures/` as run outputs. Local execution requires an OpenFOAM-enabled shell.
