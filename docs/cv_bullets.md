# Conservative Resume Bullets

## Before successful OpenFOAM reproduction

- Prepared a reproducible OpenFOAM lid-driven cavity case setup for incompressible laminar flow at `Re = 100`, including `20 x 20` and `40 x 40` mesh dictionaries, boundary-condition, transport-property, solver, and sampling files.
- Added Bash workflow scripts for mesh generation, solver execution, log capture, cleanup, and local post-processing in an OpenFOAM-enabled environment.
- Implemented Python utilities and tests for parsing `icoFoam` residual logs and converting OpenFOAM centerline sample files into CSV/plot artifacts after a real local run.
- Documented governing equations, Reynolds-number setup, solver choices, reproduction commands, output expectations, and limitations without including fabricated CFD results.

## After successful OpenFOAM reproduction

Use these bullets only after the GitHub Actions workflow or another OpenFOAM-enabled environment has produced the required logs, CSV files, and figures.

- Reproduced the `Re = 100` lid-driven cavity case with OpenFOAM `icoFoam`, including `blockMesh`, `checkMesh`, solver logging, centerline sampling, and Python post-processing.
- Archived OpenFOAM run logs, centerline velocity CSVs, residual-history plots, and centerline-profile figures as a reproducible workflow artifact.
- Added a CI output checker that fails the reproduction workflow unless required solver logs, sampled data, and figures exist and are non-empty.
