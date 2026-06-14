#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASE_DIR="$ROOT_DIR/cases/lid_driven_cavity"

find "$CASE_DIR" -maxdepth 1 -type d \
    \( -name "[1-9]*" -o -name "0.*" \) \
    -exec rm -rf {} +

rm -rf "$CASE_DIR/constant/polyMesh"
rm -rf "$CASE_DIR/postProcessing"
rm -rf "$CASE_DIR/VTK"
rm -rf "$CASE_DIR/processor"*

rm -rf "$ROOT_DIR/results/logs"
rm -f "$ROOT_DIR/results/residuals.csv"
rm -f "$ROOT_DIR/results/centerline_u.csv"
rm -f "$ROOT_DIR/results/centerline_v.csv"
rm -f "$ROOT_DIR/figures/cavity_residuals.png"
rm -f "$ROOT_DIR/figures/cavity_centerline_profiles.png"
rm -f "$ROOT_DIR/figures/cavity_velocity_magnitude.png"

mkdir -p "$ROOT_DIR/results" "$ROOT_DIR/figures"
touch "$ROOT_DIR/results/.gitkeep" "$ROOT_DIR/figures/.gitkeep"

echo "Removed generated OpenFOAM and post-processing outputs."

