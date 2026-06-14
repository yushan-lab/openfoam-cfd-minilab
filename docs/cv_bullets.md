# Resume Bullets

Use these bullets after the GitHub Actions reproduction has succeeded or after the generated results/ and figures/ outputs are present.

- Built a reproducible OpenFOAM 11 mini-project for a Re=100 lid-driven cavity, including blockMesh mesh generation, checkMesh mesh-quality inspection, physicalProperties, boundary-condition dictionaries, fvSchemes / fvSolution, and icoFoam solver execution.
- Implemented a GitHub Actions reproduction workflow with an OpenFOAM Docker image; the workflow runs blockMesh, checkMesh, icoFoam, validates required outputs, and stores solver logs, CSV summaries, and figures.
- Wrote Python post-processing utilities to parse icoFoam residual histories and extract nearest-cell centerline velocity profiles from final-time OpenFOAM field output, generating residual curves, centerline plots, and velocity-magnitude visualization.
