#!/usr/bin/env python3
"""Shared README helpers for public exporters."""

from __future__ import annotations


COMBINED_README_INTRO = """# OpenFOAM CFD Validation Lab: Laminar Cavity Verification and Paired RANS Diagnostics

This repository collects two reproducible OpenFOAM CFD studies for numerical validation and paired model diagnostics. The Re=100 lid-driven cavity component documents a four-grid OpenFOAM-10 validation workflow with exact 17-point centerline sampling and observed centerline self-convergence order from `1.86` to `2.02`.

The Pitz-Daily RANS component is a paired `kEpsilon` / `kOmegaSST` diagnostic using the same mesh, boundary conditions, numerical schemes, and common relaxation configuration. Its public continuation snapshot is `1098 / 1802` iterations with diagnostic status `quality_incomplete_comparison`; it is not a turbulence-model accuracy ranking.
"""


def ensure_combined_title_and_opening(text: str) -> str:
    marker = "## What This Project Demonstrates"
    if marker not in text:
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            rest = "\n".join(lines[1:]).lstrip()
        else:
            rest = text.lstrip()
        return COMBINED_README_INTRO.rstrip() + ("\n\n" + rest if rest else "\n")
    _, rest = text.split(marker, 1)
    return COMBINED_README_INTRO.rstrip() + "\n\n" + marker + rest


def replace_section(text: str, heading: str, replacement: str) -> str:
    marker = f"## {heading}\n"
    if marker not in text:
        raise ValueError(f"README.md does not contain the {heading} section marker.")
    before, rest = text.split(marker, 1)
    next_heading = rest.find("\n## ")
    if next_heading == -1:
        return before.rstrip() + "\n\n" + replacement.rstrip() + "\n"
    return before.rstrip() + "\n\n" + replacement.rstrip() + "\n" + rest[next_heading:]


def replace_or_insert_section_before(
    text: str,
    heading: str,
    replacement: str,
    before_heading: str,
) -> str:
    if f"## {heading}\n" in text:
        return replace_section(text, heading, replacement)
    marker = f"## {before_heading}\n"
    if marker not in text:
        return text.rstrip() + "\n\n" + replacement.rstrip() + "\n"
    before, after = text.split(marker, 1)
    return before.rstrip() + "\n\n" + replacement.rstrip() + "\n\n" + marker + after


def replace_or_insert_marked_section(
    text: str,
    start_marker: str,
    end_marker: str,
    section: str,
    before_heading: str,
) -> str:
    replacement = start_marker + "\n" + section.rstrip() + "\n" + end_marker
    if start_marker in text and end_marker in text:
        before, rest = text.split(start_marker, 1)
        _, after = rest.split(end_marker, 1)
        return before.rstrip() + "\n\n" + replacement + "\n\n" + after.lstrip("\n")
    marker = f"## {before_heading}"
    if marker not in text:
        return text.rstrip() + "\n\n" + replacement + "\n"
    before, after = text.split(marker, 1)
    return before.rstrip() + "\n\n" + replacement + "\n" + marker + after


def remove_section_if_present(text: str, heading: str) -> str:
    marker = f"## {heading}\n"
    if marker not in text:
        return text
    before, rest = text.split(marker, 1)
    next_heading = rest.find("\n## ")
    if next_heading == -1:
        return before.rstrip() + "\n"
    return before.rstrip() + "\n" + rest[next_heading:]
