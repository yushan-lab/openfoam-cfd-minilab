# Resume Bullets

After successful OpenFOAM reproduction, use these bullets only when the GitHub Actions reproduction has succeeded or when the generated `results/` and `figures/` outputs are present. Before successful OpenFOAM reproduction, do not use these result-backed bullets.

- Built a reproducible OpenFOAM 11 mini-project for a `Re = 100` lid-driven cavity, including `blockMesh` mesh generation, `checkMesh` quality inspection, `physicalProperties`, boundary-condition dictionaries, `fvSchemes` / `fvSolution`, and `icoFoam` solver execution.
- Implemented GitHub Actions reproduction with an OpenFOAM Docker image; the workflow runs `blockMesh`, `checkMesh`, `icoFoam`, validates required outputs, and stores solver logs, CSV summaries, and figures as artifacts.
- Wrote Python post-processing utilities to parse `icoFoam` residuals and extract nearest-cell centerline velocity profiles from final-time OpenFOAM field output, generating residual curves, centerline plots, and velocity-magnitude visualization.
