#!/usr/bin/env python3
"""Utilities for the formal OpenFOAM-10 pitzDaily RANS comparison workflow."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("kEpsilon", "kOmegaSST")
MODEL_COLORS = {
    "kEpsilon": "tab:blue",
    "kOmegaSST": "tab:orange",
}
WALL_PATCHES = ("upperWall", "lowerWall")
FLOW_PATCHES = ("inlet", "outlet")
CMU = 0.09

MODEL_SUMMARY_FIELDS = [
    "model",
    "display_model",
    "profile_name",
    "status",
    "result_status",
    "converged",
    "iterations",
    "wall_clock_seconds",
    "max_initial_residual_at_final_iteration",
    "residual_control_passed",
    "max_linear_solver_final_residual",
    "relative_flow_imbalance",
    "delta_p_in_minus_out_kinematic",
    "pressure_recovery_kinematic",
    "lowerWall_yplus_median",
    "lowerWall_yplus_p95",
    "x_reattachment_raw",
    "reattachment_length_normalized",
    "quality_gate_passed",
]

MANIFEST_FIELDS = [
    "model",
    "profile_name",
    "status",
    "iterations",
    "output_root",
    "wall_clock_seconds",
    "git_commit",
    "openfoam_version",
    "pair_audit_passed",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solver_profile(name: str) -> dict[str, Any]:
    profiles = {
        "official_common": {
            "profile_name": "official_common",
            "commands": ["blockMesh", "simpleFoam"],
            "max_iterations": 4000,
            "relaxation": {
                "U_exact_entry": 0.9,
                "equation_catch_all_regex": ".*",
                "equation_catch_all_value": 0.9,
            },
            "applies_to_models": list(MODELS),
        },
        "conservative_common": {
            "profile_name": "conservative_common",
            "commands": ["blockMesh", "simpleFoam"],
            "max_iterations": 8000,
            "relaxation": {
                "U_exact_entry": 0.7,
                "equation_catch_all_regex": ".*",
                "equation_catch_all_value": 0.5,
            },
            "applies_to_models": list(MODELS),
        },
    }
    if name not in profiles:
        raise ValueError(f"Unknown solver profile: {name}")
    return profiles[name]


def v1_hash_targets(v1_root: Path) -> list[Path]:
    targets: list[Path] = []
    for model, turbulence_field in [("kEpsilon", "epsilon"), ("kOmegaSST", "omega")]:
        case_dir = v1_root / model
        summary_path = case_dir / "results/solver_summary.json"
        final_time = None
        if summary_path.exists():
            final_time = json.loads(summary_path.read_text()).get("final_time")
        if not final_time and case_dir.exists():
            final_time = latest_numeric_time(case_dir)
        if final_time:
            for field in ["U", "p", "k", turbulence_field, "nut"]:
                targets.append(case_dir / str(final_time) / field)
        targets.extend(
            [
                case_dir / "results/residuals.csv",
                case_dir / "results/wall_shear_stress_values.csv",
            ]
        )
    targets.extend(
        [
            v1_root / "comparison/model_summary.csv",
            v1_root / "comparison/reattachment_summary.csv",
        ]
    )
    return targets


def write_v1_hashes(v1_root: Path, output: Path) -> dict[str, Any]:
    rows = []
    for path in v1_hash_targets(v1_root):
        rows.append(
            {
                "path": path.as_posix(),
                "exists": path.exists(),
                "sha256": sha256(path) if path.exists() else None,
                "size_bytes": path.stat().st_size if path.exists() else None,
            }
        )
    report = {"v1_root": v1_root.as_posix(), "hashes": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def turbulence_intensity(k: float, velocity_magnitude: float) -> float:
    if velocity_magnitude <= 0:
        raise ValueError("velocity magnitude must be positive")
    if k < 0:
        raise ValueError("k must be non-negative")
    return math.sqrt(2.0 * k / 3.0) / velocity_magnitude


def epsilon_length_scale(k: float, epsilon: float, cmu: float = CMU) -> float:
    if k <= 0 or epsilon <= 0 or cmu <= 0:
        raise ValueError("k, epsilon, and Cmu must be positive")
    return (cmu ** 0.75) * (k ** 1.5) / epsilon


def omega_length_scale(k: float, omega: float, cmu: float = CMU) -> float:
    if k <= 0 or omega <= 0 or cmu <= 0:
        raise ValueError("k, omega, and Cmu must be positive")
    return math.sqrt(k) / ((cmu ** 0.25) * omega)


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1e-12)


def vector_magnitude(value: Sequence[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def _remove_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def _find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unmatched brace in OpenFOAM dictionary")


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unmatched parenthesis in OpenFOAM list")


def extract_named_block(text: str, name: str) -> str | None:
    clean = _remove_comments(text)
    match = re.search(rf'(?m)(?:"{re.escape(name)}"|{re.escape(name)})\s*\{{', clean)
    if not match:
        return None
    open_index = clean.find("{", match.start())
    close_index = _find_matching_brace(clean, open_index)
    return clean[open_index + 1 : close_index]


def extract_dictionary_block(text: str, name: str) -> str | None:
    clean = _remove_comments(text)
    match = re.search(rf"(?m)\b{re.escape(name)}\s*\{{", clean)
    if not match:
        return None
    open_index = clean.find("{", match.start())
    close_index = _find_matching_brace(clean, open_index)
    return clean[open_index + 1 : close_index]


def parse_dimensions(text: str) -> str | None:
    match = re.search(r"dimensions\s+(\[[^\]]+\])\s*;", text)
    return match.group(1) if match else None


def parse_vector(value: str) -> tuple[float, float, float]:
    match = re.search(r"\(([^)]+)\)", value)
    if not match:
        raise ValueError(f"Expected vector value, got {value!r}")
    parts = [float(part) for part in match.group(1).split()]
    if len(parts) != 3:
        raise ValueError(f"Expected 3 vector components, got {parts!r}")
    return (parts[0], parts[1], parts[2])


def parse_scalar(value: str) -> float:
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", value)
    if not match:
        raise ValueError(f"Expected scalar value, got {value!r}")
    return float(match.group(0))


def _parse_uniform_value_from_line(line_value: str, internal_value: Any | None) -> Any:
    value = line_value.strip().rstrip(";")
    if value == "$internalField":
        return internal_value
    if value.startswith("uniform"):
        payload = value[len("uniform") :].strip()
        if payload.startswith("("):
            return parse_vector(payload)
        return parse_scalar(payload)
    if value.startswith("("):
        return parse_vector(value)
    return parse_scalar(value)


def parse_field(path: Path) -> dict[str, Any]:
    text = path.read_text(errors="ignore")
    dimensions = parse_dimensions(text)
    internal_match = re.search(r"internalField\s+([^;]+);", text, flags=re.DOTALL)
    internal_value = None
    if internal_match:
        internal_value = _parse_uniform_value_from_line(internal_match.group(1), None)

    boundary: dict[str, dict[str, Any]] = {}
    boundary_block = extract_dictionary_block(text, "boundaryField")
    if boundary_block:
        for patch_match in re.finditer(r"(?m)^\s*(\w+)\s*\{", boundary_block):
            patch = patch_match.group(1)
            open_index = boundary_block.find("{", patch_match.start())
            close_index = _find_matching_brace(boundary_block, open_index)
            patch_block = boundary_block[open_index + 1 : close_index]
            type_match = re.search(r"\btype\s+([^;]+);", patch_block)
            value_match = re.search(r"\bvalue\s+([^;]+);", patch_block, flags=re.DOTALL)
            boundary[patch] = {
                "type": type_match.group(1).strip() if type_match else None,
                "value": _parse_uniform_value_from_line(value_match.group(1), internal_value)
                if value_match
                else None,
            }
    return {
        "path": path,
        "dimensions": dimensions,
        "internalField": internal_value,
        "boundaryField": boundary,
    }


def residual_control_thresholds(fv_solution_text: str, fields: Iterable[str]) -> dict[str, float | None]:
    residual_block = extract_dictionary_block(fv_solution_text, "residualControl") or ""
    entries: list[tuple[str, float]] = []
    for match in re.finditer(
        r'(?m)^\s*("([^"]+)"|[A-Za-z_][A-Za-z0-9_]*)\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*;',
        residual_block,
    ):
        raw = match.group(2) if match.group(2) is not None else match.group(1)
        entries.append((raw, float(match.group(3))))

    result: dict[str, float | None] = {}
    for field in fields:
        threshold = None
        for pattern, value in entries:
            if pattern == field:
                threshold = value
            else:
                try:
                    if re.fullmatch(pattern, field):
                        threshold = value
                except re.error:
                    pass
        result[field] = threshold
    return result


def _momentum_transport_model(path: Path) -> str | None:
    match = re.search(r"\bmodel\s+([^;]+);", path.read_text(errors="ignore"))
    return match.group(1).strip() if match else None


def _fv_schemes_convection_terms(path: Path) -> dict[str, bool]:
    text = path.read_text(errors="ignore")
    return {
        "div(phi,k)": "div(phi,k)" in text,
        "div(phi,epsilon)": "div(phi,epsilon)" in text,
        "div(phi,omega)": "div(phi,omega)" in text,
    }


def audit_turbulence_initialization(
    k_epsilon_case: Path,
    k_omega_case: Path,
    cmu: float = CMU,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    eps_u = parse_field(k_epsilon_case / "0/U")
    sst_u = parse_field(k_omega_case / "0/U")
    eps_k = parse_field(k_epsilon_case / "0/k")
    sst_k = parse_field(k_omega_case / "0/k")
    epsilon = parse_field(k_epsilon_case / "0/epsilon")
    omega = parse_field(k_omega_case / "0/omega")

    eps_inlet_u = tuple(eps_u["boundaryField"]["inlet"]["value"])
    sst_inlet_u = tuple(sst_u["boundaryField"]["inlet"]["value"])
    eps_inlet_k = float(eps_k["boundaryField"]["inlet"]["value"])
    sst_inlet_k = float(sst_k["boundaryField"]["inlet"]["value"])
    eps_inlet_epsilon = float(epsilon["boundaryField"]["inlet"]["value"])
    sst_inlet_omega = float(omega["boundaryField"]["inlet"]["value"])

    eps_l = epsilon_length_scale(eps_inlet_k, eps_inlet_epsilon, cmu)
    sst_l = omega_length_scale(sst_inlet_k, sst_inlet_omega, cmu)
    diff = relative_difference(eps_l, sst_l)
    eps_i = turbulence_intensity(eps_inlet_k, vector_magnitude(eps_inlet_u))
    sst_i = turbulence_intensity(sst_inlet_k, vector_magnitude(sst_inlet_u))

    eps_nut = parse_field(k_epsilon_case / "0/nut")
    sst_nut = parse_field(k_omega_case / "0/nut")
    fv_solution_text = (k_epsilon_case / "system/fvSolution").read_text(errors="ignore")
    residual_thresholds = residual_control_thresholds(fv_solution_text, ["U", "p", "k", "epsilon", "omega"])
    rows: list[dict[str, Any]] = []
    for model, fields in [
        ("kEpsilon", {"U": eps_u, "k": eps_k, "epsilon": epsilon, "nut": eps_nut}),
        ("kOmegaSST", {"U": sst_u, "k": sst_k, "omega": omega, "nut": sst_nut}),
    ]:
        for field, parsed in fields.items():
            for patch, data in parsed["boundaryField"].items():
                rows.append(
                    {
                        "model": model,
                        "field": field,
                        "patch": patch,
                        "type": data.get("type"),
                        "value": _jsonable(data.get("value")),
                        "dimensions": parsed.get("dimensions"),
                    }
                )

    report = {
        "inlet_U": {"kEpsilon": eps_inlet_u, "kOmegaSST": sst_inlet_u},
        "inlet_k": {"kEpsilon": eps_inlet_k, "kOmegaSST": sst_inlet_k},
        "inlet_U_match": eps_inlet_u == sst_inlet_u,
        "inlet_k_match": math.isclose(eps_inlet_k, sst_inlet_k, rel_tol=1e-12, abs_tol=1e-12),
        "epsilon_positive": math.isfinite(eps_inlet_epsilon) and eps_inlet_epsilon > 0,
        "omega_positive": math.isfinite(sst_inlet_omega) and sst_inlet_omega > 0,
        "turbulence_intensity": {"kEpsilon": eps_i, "kOmegaSST": sst_i},
        "turbulence_intensity_match": math.isclose(eps_i, sst_i, rel_tol=1e-12, abs_tol=1e-12),
        "length_scale": {"kEpsilon_epsilon": eps_l, "kOmegaSST_omega": sst_l},
        "length_scale_relative_difference": diff,
        "length_scale_gate_passed": diff <= 0.20,
        "auto_corrected_inputs": False,
        "wall_function_checks": {
            "kEpsilon": {
                "k": _patch_types(eps_k, WALL_PATCHES),
                "epsilon": _patch_types(epsilon, WALL_PATCHES),
                "nut": _patch_types(eps_nut, WALL_PATCHES),
            },
            "kOmegaSST": {
                "k": _patch_types(sst_k, WALL_PATCHES),
                "omega": _patch_types(omega, WALL_PATCHES),
                "nut": _patch_types(sst_nut, WALL_PATCHES),
            },
        },
        "outlet_turbulence_bc": {
            "kEpsilon": {"k": eps_k["boundaryField"]["outlet"]["type"], "epsilon": epsilon["boundaryField"]["outlet"]["type"]},
            "kOmegaSST": {"k": sst_k["boundaryField"]["outlet"]["type"], "omega": omega["boundaryField"]["outlet"]["type"]},
        },
        "momentumTransport_models": {
            "kEpsilon": _momentum_transport_model(k_epsilon_case / "constant/momentumTransport"),
            "kOmegaSST": _momentum_transport_model(k_omega_case / "constant/momentumTransport"),
        },
        "fvSchemes_convection_terms": _fv_schemes_convection_terms(k_epsilon_case / "system/fvSchemes"),
        "fvSolution_residualControl": residual_thresholds,
        "residual_control_gate_passed": all(
            residual_thresholds.get(field) is not None for field in ["U", "p", "k", "epsilon", "omega"]
        )
        and residual_thresholds.get("epsilon") == residual_thresholds.get("omega"),
    }

    report["wall_function_gate_passed"] = (
        set(report["wall_function_checks"]["kEpsilon"]["k"].values()) == {"kqRWallFunction"}
        and set(report["wall_function_checks"]["kEpsilon"]["epsilon"].values()) == {"epsilonWallFunction"}
        and set(report["wall_function_checks"]["kEpsilon"]["nut"].values()) == {"nutkWallFunction"}
        and set(report["wall_function_checks"]["kOmegaSST"]["k"].values()) == {"kqRWallFunction"}
        and set(report["wall_function_checks"]["kOmegaSST"]["omega"].values()) == {"omegaWallFunction"}
        and set(report["wall_function_checks"]["kOmegaSST"]["nut"].values()) == {"nutkWallFunction"}
    )
    report["gate_passed"] = all(
        [
            report["inlet_U_match"],
            report["inlet_k_match"],
            report["turbulence_intensity_match"],
            report["epsilon_positive"],
            report["omega_positive"],
            report["length_scale_gate_passed"],
            report["wall_function_gate_passed"],
            report["residual_control_gate_passed"],
        ]
    )
    return report, rows


def _patch_types(field: dict[str, Any], patches: Iterable[str]) -> dict[str, str | None]:
    return {patch: field["boundaryField"].get(patch, {}).get("type") for patch in patches}


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def write_turbulence_audit(output_dir: Path, report: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "turbulence_initialization_audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_jsonable) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "boundary_condition_audit.csv", rows)


@dataclass
class PostprocessCapability:
    name: str
    template_path: str | None
    command: str
    output_location: str
    dimensions: str | None
    available: bool
    smoke_status: str


def postprocess_capability_report(capabilities: Iterable[PostprocessCapability]) -> dict[str, Any]:
    return {"capabilities": [asdict(capability) for capability in capabilities]}


def discover_postprocess_capabilities(case_dir: Path | None = None) -> dict[str, Any]:
    wm_project_dir = os.environ.get("WM_PROJECT_DIR")
    template_root = Path(wm_project_dir) / "etc/caseDicts/postProcessing" if wm_project_dir else None
    template_map = {
        "yPlus": "fields/yPlus",
        "wallShearStress": "fields/wallShearStress",
        "patchFlowRate": "surfaceFieldValue/patchFlowRate",
        "patchAverage": "surfaceFieldValue/patchAverage",
        "boundaryProbes": "probes/boundaryProbes",
        "patchSurface": "surface/patchSurface",
        "cutPlaneSurface": "surface/cutPlaneSurface",
        "residuals": "numerical/residuals",
        "writeCellCentres": "fields/writeCellCentres",
        "writeCellVolumes": "fields/writeCellVolumes",
    }
    commands = {
        "yPlus": "simpleFoam -case CASE -postProcess -func yPlus -latestTime",
        "wallShearStress": "simpleFoam -case CASE -postProcess -func wallShearStress -latestTime",
        "patchFlowRate": "postProcess -case CASE -func 'patchFlowRate(phi,patch=inlet)' -latestTime",
        "patchAverage": "postProcess -case CASE -func 'patchAverage(p,patch=inlet)' -latestTime",
        "boundaryProbes": "postProcess -case CASE -func boundaryProbes -latestTime",
        "patchSurface": "postProcess -case CASE -func patchSurface -latestTime",
        "cutPlaneSurface": "postProcess -case CASE -func cutPlaneSurface -latestTime",
        "residuals": "postProcess -case CASE -func residuals -latestTime",
        "writeCellCentres": "postProcess -case CASE -func writeCellCentres -latestTime",
        "writeCellVolumes": "postProcess -case CASE -func writeCellVolumes -latestTime",
    }
    output_locations = {
        "yPlus": "CASE/<time>/yPlus",
        "wallShearStress": "CASE/<time>/wallShearStress",
        "patchFlowRate": "CASE/postProcessing/patchFlowRate(phi,patch=<patch>)/<time>/surfaceFieldValue.dat",
        "patchAverage": "CASE/postProcessing/patchAverage(p,patch=<patch>)/<time>/surfaceFieldValue.dat",
        "boundaryProbes": "CASE/postProcessing/boundaryProbes/<time>/",
        "patchSurface": "CASE/postProcessing/patchSurface/<time>/",
        "cutPlaneSurface": "CASE/postProcessing/cutPlaneSurface/<time>/",
        "residuals": "CASE/postProcessing/residuals/<time>/residuals.dat",
        "writeCellCentres": "CASE/<time>/C",
        "writeCellVolumes": "CASE/<time>/V",
    }
    dimensions = {
        "yPlus": "[0 0 0 0 0 0 0]",
        "wallShearStress": None,
        "patchFlowRate": None,
        "patchAverage": None,
        "boundaryProbes": None,
        "patchSurface": None,
        "cutPlaneSurface": None,
        "residuals": None,
        "writeCellCentres": "[0 1 0 0 0 0 0]",
        "writeCellVolumes": "[0 3 0 0 0 0 0]",
    }

    final_time = latest_numeric_time(case_dir) if case_dir and case_dir.exists() else None

    def supplied_case_status(name: str) -> tuple[str, str | None]:
        if case_dir is None:
            return "not_run", dimensions[name]
        if final_time is None:
            return "case_supplied_no_numeric_time", dimensions[name]
        time_dir = case_dir / final_time
        if name == "yPlus":
            path = time_dir / "yPlus"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(path.read_text(errors="ignore")) if path.exists() else dimensions[name],
            )
        if name == "wallShearStress":
            path = time_dir / "wallShearStress"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(path.read_text(errors="ignore")) if path.exists() else dimensions[name],
            )
        if name == "patchFlowRate":
            path = _postprocessing_dat(case_dir, "patchFlowRate(phi,patch=inlet)", final_time)
            phi_path = time_dir / "phi"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(phi_path.read_text(errors="ignore")) if phi_path.exists() else dimensions[name],
            )
        if name == "patchAverage":
            path = _postprocessing_dat(case_dir, "patchAverage(p,patch=inlet)", final_time)
            p_path = time_dir / "p"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(p_path.read_text(errors="ignore")) if p_path.exists() else dimensions[name],
            )
        if name == "writeCellCentres":
            path = time_dir / "C"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(path.read_text(errors="ignore")) if path.exists() else dimensions[name],
            )
        if name == "writeCellVolumes":
            path = time_dir / "V"
            return (
                "succeeded_on_supplied_case" if path.exists() and path.stat().st_size > 0 else "missing_on_supplied_case",
                parse_dimensions(path.read_text(errors="ignore")) if path.exists() else dimensions[name],
            )
        return "not_run", dimensions[name]

    capabilities: list[PostprocessCapability] = []
    for name, rel in template_map.items():
        template_path = str(template_root / rel) if template_root else None
        available = bool(template_root and (template_root / rel).exists())
        smoke_status, actual_dimensions = supplied_case_status(name)
        capabilities.append(
            PostprocessCapability(
                name=name,
                template_path=template_path,
                command=commands[name],
                output_location=output_locations[name],
                dimensions=actual_dimensions,
                available=available,
                smoke_status=smoke_status,
            )
        )
    return postprocess_capability_report(capabilities)


def relative_flow_imbalance(rows: Iterable[dict[str, Any]]) -> float:
    values = {row["patch"]: abs(float(row["signed_volumetric_flow_rate"])) for row in rows}
    if "inlet" not in values or "outlet" not in values:
        raise ValueError("Expected inlet and outlet flow rows")
    return abs(values["inlet"] - values["outlet"]) / max(values["inlet"], values["outlet"], 1e-12)


def flow_rate_quantity_label(field_dimensions: str | None) -> str:
    if field_dimensions == "[0 3 -1 0 0 0 0]":
        return "volumetric flow rate"
    return f"flow rate with dimensions {field_dimensions or 'unknown'}"


def pressure_quantity_label(field_dimensions: str | None) -> str:
    if field_dimensions == "[0 2 -2 0 0 0 0]":
        return "kinematic pressure"
    return f"pressure-like field with dimensions {field_dimensions or 'unknown'}"


def pressure_recovery_kinematic(p_in: float, p_out: float) -> float:
    return p_out - p_in


def result_status_for_model(status: str | None) -> str:
    return "complete_converged" if status == "converged" else "provisional_incomplete_convergence"


def model_display_name(model: str, status: str | None) -> str:
    return model if status == "converged" else f"{model} (not converged)"


def streamwise_projection(vector: Sequence[float], inlet_u: Sequence[float]) -> float:
    magnitude = vector_magnitude(inlet_u)
    if magnitude <= 0:
        raise ValueError("inlet velocity magnitude must be positive")
    return sum(vector[i] * inlet_u[i] / magnitude for i in range(3))


def wall_shear_row(
    model: str,
    patch: str,
    face_index: int,
    centre: Sequence[float],
    tau: Sequence[float],
    inlet_u: Sequence[float],
    dimensions: str | None,
) -> dict[str, Any]:
    global_streamwise = streamwise_projection(tau, inlet_u)
    return {
        "model": model,
        "patch": patch,
        "face_index": face_index,
        "x": centre[0],
        "y": centre[1],
        "z": centre[2],
        "tau_x": tau[0],
        "tau_y": tau[1],
        "tau_z": tau[2],
        "tau_streamwise": global_streamwise,
        "tau_global_streamwise": global_streamwise,
        "tau_downstream_tangent": global_streamwise,
        "tau_tangent_minus_global_streamwise": 0.0,
        "field_dimensions": dimensions,
    }


def _finite_values(values: Iterable[float]) -> tuple[list[float], int]:
    raw = list(values)
    finite = [float(value) for value in raw if math.isfinite(float(value))]
    return finite, len(raw)


def percentile(values: Sequence[float], percent: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_values(values: Iterable[float]) -> dict[str, Any]:
    finite, raw_count = _finite_values(values)
    if not finite:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "p05": None,
            "p95": None,
            "max": None,
            "finite_fraction": 0.0 if raw_count else None,
        }
    return {
        "count": len(finite),
        "min": min(finite),
        "median": statistics.median(finite),
        "mean": statistics.fmean(finite),
        "p05": percentile(finite, 5),
        "p95": percentile(finite, 95),
        "max": max(finite),
        "finite_fraction": len(finite) / raw_count if raw_count else None,
    }


def _tau_for_reattachment(row: dict[str, Any]) -> float:
    if row.get("tau_downstream_tangent") not in {None, ""}:
        return float(row["tau_downstream_tangent"])
    return float(row["tau_streamwise"])


def _sign(value: float, tolerance: float) -> int:
    if value > tolerance:
        return 1
    if value < -tolerance:
        return -1
    return 0


def _sustained_run_exists(signs: list[int], target_sign: int, min_count: int) -> bool:
    count = 0
    for sign in signs:
        if sign == target_sign:
            count += 1
            if count >= min_count:
                return True
        elif sign != 0:
            count = 0
    return False


def detect_reattachment(
    rows: Iterable[dict[str, Any]],
    geometry: dict[str, Any] | None = None,
    min_pre_count: int = 10,
    min_post_count: int = 10,
) -> dict[str, Any]:
    geometry = geometry or {}
    step_x = float(geometry.get("step_x") or 0.0)
    step_height = geometry.get("step_height")
    step_height_value = float(step_height) if step_height not in {None, 0} else None
    upstream = sorted(
        [
            row
            for row in rows
            if float(row["x"]) < step_x and math.isfinite(_tau_for_reattachment(row))
        ],
        key=lambda row: float(row["x"]),
    )
    if len(upstream) < 3:
        return _reattachment_not_detected("insufficient_upstream_attached_samples")
    upstream_values = [_tau_for_reattachment(row) for row in upstream]
    upstream_median = statistics.median(upstream_values)
    tolerance = max(abs(upstream_median) * 1e-6, 1e-12)
    attached_sign = _sign(upstream_median, tolerance)
    if attached_sign == 0:
        result = _reattachment_not_detected("upstream_attached_median_near_zero")
        result["upstream_attached_median"] = upstream_median
        return result
    separated_sign = -attached_sign

    downstream = downstream_lower_wall_rows(rows, {"step_x": step_x})
    points = [
        {
            "x": float(row["x"]),
            "tau": _tau_for_reattachment(row),
            "sign": _sign(_tau_for_reattachment(row), tolerance),
        }
        for row in downstream
        if math.isfinite(float(row["x"])) and math.isfinite(_tau_for_reattachment(row))
    ]
    all_crossings: list[dict[str, Any]] = []
    for index in range(1, len(points)):
        prev = points[index - 1]
        curr = points[index]
        if prev["sign"] != separated_sign or curr["sign"] != attached_sign:
            continue

        pre_start = index - 1
        while pre_start - 1 >= 0 and points[pre_start - 1]["sign"] == separated_sign:
            pre_start -= 1
        pre_count = index - pre_start
        pre_length = points[index - 1]["x"] - points[pre_start]["x"] if pre_count > 1 else 0.0

        post_end = index
        while post_end < len(points) and points[post_end]["sign"] == attached_sign:
            post_end += 1
        post_count = post_end - index
        later_sustained_separated = _sustained_run_exists(
            [point["sign"] for point in points[post_end:]],
            separated_sign,
            min_pre_count,
        )

        pre_gate = pre_count >= min_pre_count or (
            step_height_value is not None and pre_length >= 0.5 * step_height_value
        )
        post_gate = post_count >= min_post_count
        selected_eligible = pre_gate and post_gate and not later_sustained_separated
        denominator = curr["tau"] - prev["tau"]
        x_cross = curr["x"] if abs(denominator) < 1e-30 else prev["x"] + (-prev["tau"] / denominator) * (curr["x"] - prev["x"])
        all_crossings.append(
            {
                "crossing_index": len(all_crossings),
                "direction": "negative-to-positive" if attached_sign > 0 else "positive-to-negative",
                "x_crossing": x_cross,
                "pre_separated_count": pre_count,
                "pre_separated_length": pre_length,
                "post_attached_count": post_count,
                "later_sustained_separated": later_sustained_separated,
                "selected_eligible": selected_eligible,
                "rejection_reason": None
                if selected_eligible
                else _crossing_rejection_reason(pre_gate, post_gate, later_sustained_separated),
            }
        )

    eligible = [crossing for crossing in all_crossings if crossing["selected_eligible"]]
    if not eligible:
        return {
            "status": "not_detected",
            "attached_sign": attached_sign,
            "separated_sign": separated_sign,
            "upstream_attached_median": upstream_median,
            "all_crossings": all_crossings,
            "selected_crossing": None,
            "x_reattachment_raw": None,
            "reattachment_length_normalized": None,
            "confidence_status": "no_stable_recovery",
        }
    selected = eligible[-1]
    normalized = None
    if step_height_value is not None:
        normalized = (selected["x_crossing"] - step_x) / step_height_value
    return {
        "status": "detected",
        "attached_sign": attached_sign,
        "separated_sign": separated_sign,
        "upstream_attached_median": upstream_median,
        "all_crossings": all_crossings,
        "selected_crossing": selected,
        "x_reattachment_raw": selected["x_crossing"],
        "reattachment_length_normalized": normalized,
        "confidence_status": "selected_final_stable_recovery",
    }


def _crossing_rejection_reason(pre_gate: bool, post_gate: bool, later_sustained_separated: bool) -> str:
    if not pre_gate:
        return "insufficient_sustained_separation_before_crossing"
    if not post_gate:
        return "insufficient_attached_faces_after_crossing"
    if later_sustained_separated:
        return "later_sustained_separated_region_detected"
    return "unknown"


def _reattachment_not_detected(reason: str) -> dict[str, Any]:
    return {
        "status": "not_detected",
        "attached_sign": None,
        "separated_sign": None,
        "upstream_attached_median": None,
        "all_crossings": [],
        "selected_crossing": None,
        "x_reattachment_raw": None,
        "reattachment_length_normalized": None,
        "confidence_status": reason,
    }


def downstream_lower_wall_rows(rows: Iterable[dict[str, Any]], geometry: dict[str, Any]) -> list[dict[str, Any]]:
    step_x = geometry.get("step_x")
    if step_x is None:
        return sorted(rows, key=lambda row: float(row["x"]))
    tolerance = max(abs(float(step_x)) * 1e-9, 1e-12)
    return sorted(
        [row for row in rows if float(row["x"]) > float(step_x) + tolerance],
        key=lambda row: float(row["x"]),
    )


def annotate_local_wall_tangent(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_patch: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_patch.setdefault(str(row["patch"]), []).append(row)
    for patch_rows in by_patch.values():
        ordered = sorted(patch_rows, key=lambda row: (float(row["x"]), float(row["y"]), int(row["face_index"])))
        for index, row in enumerate(ordered):
            if len(ordered) == 1:
                tangent = (1.0, 0.0, 0.0)
            elif index == 0:
                tangent = _unit_vector_between(row, ordered[index + 1])
            elif index == len(ordered) - 1:
                tangent = _unit_vector_between(ordered[index - 1], row)
            else:
                tangent = _unit_vector_between(ordered[index - 1], ordered[index + 1])
            if tangent[0] < 0:
                tangent = tuple(-value for value in tangent)
            tau = (float(row["tau_x"]), float(row["tau_y"]), float(row["tau_z"]))
            local = sum(tau[i] * tangent[i] for i in range(3))
            row["local_wall_tangent_x"] = tangent[0]
            row["local_wall_tangent_y"] = tangent[1]
            row["local_wall_tangent_z"] = tangent[2]
            row["tau_downstream_tangent"] = local
            row["tau_tangent_minus_global_streamwise"] = local - float(row["tau_global_streamwise"])
    return rows


def _unit_vector_between(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, float, float]:
    vector = (
        float(right["x"]) - float(left["x"]),
        float(right["y"]) - float(left["y"]),
        float(right["z"]) - float(left["z"]),
    )
    magnitude = vector_magnitude(vector)
    if magnitude <= 0:
        return (1.0, 0.0, 0.0)
    return (vector[0] / magnitude, vector[1] / magnitude, vector[2] / magnitude)


def audit_geometry_from_block_mesh_text(text: str) -> dict[str, Any]:
    vertices_match = re.search(r"vertices\s*\((.*?)\)\s*;", _remove_comments(text), flags=re.DOTALL)
    if not vertices_match:
        return _empty_geometry_audit()
    vertices = [parse_vector(match.group(0)) for match in re.finditer(r"\([^()]+\)", vertices_match.group(1))]
    if not vertices:
        return _empty_geometry_audit()

    xs = sorted({round(vertex[0], 12) for vertex in vertices})
    ys_at_zero = sorted({vertex[1] for vertex in vertices if abs(vertex[0]) < 1e-12})
    negative_ys = [y for y in ys_at_zero if y < 0]
    nonnegative_ys = [y for y in ys_at_zero if y >= 0]
    convert = 1.0
    convert_match = re.search(r"convertToMeters\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*;", text)
    if convert_match:
        convert = float(convert_match.group(1))
    if 0.0 in xs and negative_ys and nonnegative_ys:
        step_height = (min(nonnegative_ys) - min(negative_ys)) * convert
        return {
            "step_x": 0.0,
            "step_x_status": "detected",
            "step_height": step_height if step_height > 0 else None,
            "step_height_status": "detected" if step_height > 0 else "not_detected",
            "streamwise_coordinate": "x",
            "source": "blockMeshDict vertices",
        }
    return _empty_geometry_audit()


def _empty_geometry_audit() -> dict[str, Any]:
    return {
        "step_x": None,
        "step_x_status": "not_detected",
        "step_height": None,
        "step_height_status": "not_detected",
        "streamwise_coordinate": "x",
        "source": "insufficient blockMeshDict evidence",
    }


def classify_solver_status(summary: dict[str, Any], solver_exit_code: int) -> str:
    if solver_exit_code != 0 or summary.get("failed"):
        return "failed"
    if summary.get("converged") or summary.get("simple_solution_converged"):
        return "converged"
    if summary.get("ended", True):
        return "max_iterations_reached"
    return "failed"


def manifest_row(
    model: str,
    status: str,
    iterations: int | None,
    output_root: Path,
    wall_clock_seconds: float,
    git_commit: str | None,
    openfoam_version: str | None,
    pair_audit_passed: bool,
    profile_name: str | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "profile_name": profile_name,
        "status": status,
        "iterations": iterations,
        "output_root": str(output_root),
        "wall_clock_seconds": wall_clock_seconds,
        "git_commit": git_commit,
        "openfoam_version": openfoam_version,
        "pair_audit_passed": pair_audit_passed,
    }


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    write_csv(path, rows, MANIFEST_FIELDS)


def quality_gate_status(gates: dict[str, bool], yplus_summary: dict[str, Any] | None = None) -> str:
    required = [
        "blockMesh",
        "checkMesh",
        "pair_audit",
        "simpleFoam",
        "no_solver_failures",
        "final_fields",
        "residual_parser",
        "flow_balance",
        "yPlus",
        "wallShearStress",
        "patch_pressure",
    ]
    if not all(bool(gates.get(name)) for name in required):
        return "failed"
    if not gates.get("simple_solution_converged", False):
        return "incomplete_convergence"
    return "passed"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latest_numeric_time(case_dir: Path) -> str | None:
    numeric: list[tuple[float, str]] = []
    for path in case_dir.iterdir():
        if not path.is_dir() or path.name == "0":
            continue
        try:
            numeric.append((float(path.name), path.name))
        except ValueError:
            continue
    if not numeric:
        return None
    return max(numeric, key=lambda item: item[0])[1]


def read_openfoam_list(path: Path, kind: str) -> list[Any]:
    text = _remove_comments(path.read_text(errors="ignore"))
    count_match = re.search(r"(?m)^\s*([0-9]+)\s*\n\s*\(", text)
    if not count_match:
        return []
    open_index = text.find("(", count_match.end() - 1)
    close_index = _find_matching_paren(text, open_index)
    body = text[open_index + 1 : close_index]
    if kind == "vector":
        return [parse_vector(match.group(0)) for match in re.finditer(r"\([^()]+\)", body)]
    if kind == "face":
        faces = []
        for match in re.finditer(r"([0-9]+)\(([^()]*)\)", body):
            faces.append([int(value) for value in match.group(2).split()])
        return faces
    raise ValueError(f"Unsupported OpenFOAM list kind: {kind}")


def parse_poly_boundary(path: Path) -> dict[str, dict[str, int]]:
    text = path.read_text(errors="ignore")
    patches: dict[str, dict[str, int]] = {}
    for patch_match in re.finditer(r"(?m)^\s*(\w+)\s*\{", text):
        patch = patch_match.group(1)
        if patch in {"FoamFile"}:
            continue
        open_index = text.find("{", patch_match.start())
        close_index = _find_matching_brace(text, open_index)
        block = text[open_index + 1 : close_index]
        n_faces = re.search(r"\bnFaces\s+([0-9]+)\s*;", block)
        start_face = re.search(r"\bstartFace\s+([0-9]+)\s*;", block)
        if n_faces and start_face:
            patches[patch] = {"nFaces": int(n_faces.group(1)), "startFace": int(start_face.group(1))}
    return patches


def patch_face_centres(case_dir: Path, patch: str) -> list[tuple[float, float, float]]:
    boundary = parse_poly_boundary(case_dir / "constant/polyMesh/boundary")
    if patch not in boundary:
        return []
    points = read_openfoam_list(case_dir / "constant/polyMesh/points", "vector")
    faces = read_openfoam_list(case_dir / "constant/polyMesh/faces", "face")
    info = boundary[patch]
    centres = []
    for face in faces[info["startFace"] : info["startFace"] + info["nFaces"]]:
        coords = [points[index] for index in face]
        centres.append(tuple(sum(coord[i] for coord in coords) / len(coords) for i in range(3)))
    return centres


def _parse_nonuniform_values(block: str) -> list[Any] | None:
    match = re.search(r"(?:value\s+)?nonuniform\s+List<(\w+)>\s+([0-9]+)\s*\(", block, flags=re.DOTALL)
    if not match:
        return None
    kind = match.group(1)
    open_index = block.find("(", match.end() - 1)
    close_index = _find_matching_paren(block, open_index)
    body = block[open_index + 1 : close_index]
    if kind == "scalar":
        return [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", body)]
    if kind == "vector":
        return [parse_vector(item.group(0)) for item in re.finditer(r"\([^()]+\)", body)]
    return None


def read_patch_field_values(field_path: Path, patch: str) -> tuple[str | None, list[Any]]:
    text = field_path.read_text(errors="ignore")
    dimensions = parse_dimensions(text)
    boundary_block = extract_dictionary_block(text, "boundaryField")
    if not boundary_block:
        values = _parse_nonuniform_values(text)
        return dimensions, values or []
    patch_block = extract_named_block(boundary_block, patch)
    if patch_block is None:
        return dimensions, []
    values = _parse_nonuniform_values(patch_block)
    if values is not None:
        return dimensions, values
    value_match = re.search(r"\bvalue\s+uniform\s+([^;]+);", patch_block)
    if value_match:
        payload = value_match.group(1).strip()
        if payload.startswith("("):
            return dimensions, [parse_vector(payload)]
        return dimensions, [parse_scalar(payload)]
    return dimensions, []


def parse_surface_value_dat(path: Path) -> float | None:
    if not path.exists():
        return None
    last_value = None
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            try:
                last_value = float(parts[-1])
            except ValueError:
                pass
    return last_value


def _postprocessing_dat(case_dir: Path, function_name: str, time_name: str) -> Path:
    return case_dir / "postProcessing" / function_name / time_name / "surfaceFieldValue.dat"


def collect_patch_flow_rates(case_dir: Path, model: str, final_time: str) -> tuple[list[dict[str, Any]], float | None]:
    phi_path = case_dir / final_time / "phi"
    dimensions = parse_dimensions(phi_path.read_text(errors="ignore")) if phi_path.exists() else None
    rows = []
    for patch in FLOW_PATCHES:
        function_name = f"patchFlowRate(phi,patch={patch})"
        value = parse_surface_value_dat(_postprocessing_dat(case_dir, function_name, final_time))
        rows.append(
            {
                "model": model,
                "patch": patch,
                "signed_volumetric_flow_rate": value,
                "absolute_volumetric_flow_rate": abs(value) if value is not None else None,
                "field_dimensions": dimensions,
                "final_time": final_time,
            }
        )
    imbalance = None
    if all(row["signed_volumetric_flow_rate"] is not None for row in rows):
        imbalance = relative_flow_imbalance(rows)
    return rows, imbalance


def collect_patch_pressure(case_dir: Path, model: str, final_time: str) -> dict[str, Any]:
    p_path = case_dir / final_time / "p"
    if not p_path.exists():
        p_path = case_dir / "0/p"
    dimensions = parse_dimensions(p_path.read_text(errors="ignore")) if p_path.exists() else None
    values = {}
    for patch in FLOW_PATCHES:
        function_name = f"patchAverage(p,patch={patch})"
        values[patch] = parse_surface_value_dat(_postprocessing_dat(case_dir, function_name, final_time))
    delta = None
    recovery = None
    if values.get("inlet") is not None and values.get("outlet") is not None:
        delta = values["inlet"] - values["outlet"]
        recovery = pressure_recovery_kinematic(values["inlet"], values["outlet"])
    return {
        "model": model,
        "p_inlet_area_average": values.get("inlet"),
        "p_outlet_area_average": values.get("outlet"),
        "delta_p_in_minus_out_kinematic": delta,
        "pressure_recovery_kinematic": recovery,
        "delta_p_kinematic": delta,
        "field_dimensions": dimensions,
    }


def collect_yplus(case_dir: Path, model: str, final_time: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    field_path = case_dir / final_time / "yPlus"
    value_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    if not field_path.exists():
        return value_rows, summary_rows
    for patch in WALL_PATCHES:
        dimensions, values = read_patch_field_values(field_path, patch)
        scalars = [float(value) for value in values if not isinstance(value, tuple)]
        for index, value in enumerate(scalars):
            value_rows.append(
                {
                    "model": model,
                    "patch": patch,
                    "face_index": index,
                    "yplus": value,
                    "field_dimensions": dimensions,
                }
            )
        summary = summarize_values(scalars)
        summary_rows.append({"model": model, "patch": patch, **summary, "field_dimensions": dimensions})
    return value_rows, summary_rows


def collect_wall_shear(case_dir: Path, model: str, final_time: str) -> list[dict[str, Any]]:
    field_path = case_dir / final_time / "wallShearStress"
    if not field_path.exists():
        return []
    inlet_u = parse_field(case_dir / "0/U")["boundaryField"]["inlet"]["value"]
    rows: list[dict[str, Any]] = []
    for patch in WALL_PATCHES:
        dimensions, vectors = read_patch_field_values(field_path, patch)
        centres = patch_face_centres(case_dir, patch)
        for index, tau in enumerate(vectors):
            if not isinstance(tau, tuple) or index >= len(centres):
                continue
            rows.append(wall_shear_row(model, patch, index, centres[index], tau, inlet_u, dimensions))
    return annotate_local_wall_tangent(rows)


def collect_reattachment(case_dir: Path, wall_shear_rows: list[dict[str, Any]]) -> dict[str, Any]:
    geometry = audit_geometry_from_block_mesh_text((case_dir / "system/blockMeshDict").read_text(errors="ignore"))
    lower_wall_rows = [row for row in wall_shear_rows if row["patch"] == "lowerWall"]
    reattachment = detect_reattachment(lower_wall_rows, geometry)
    return {"geometry": geometry, **reattachment}


def reverse_flow_summary(case_dir: Path, model: str, final_time: str) -> dict[str, Any]:
    return {
        "model": model,
        "status": "not_run",
        "reason": "cell-centred reverse-flow volume parsing is optional for this stage",
        "final_time": final_time,
    }


def max_final_residual(final_residuals: dict[str, Any]) -> float | None:
    values = [
        float(details["final_residual"])
        for details in final_residuals.values()
        if isinstance(details, dict) and details.get("final_residual") is not None
    ]
    return max(values) if values else None


def residual_threshold_for_field(field: str) -> float:
    if field == "p":
        return 1e-2
    if field in {"Ux", "Uy", "Uz", "U"}:
        return 1e-3
    return 1e-3


def residual_color_for_model_field(model: str, field: str) -> str:
    if field == "epsilon" and model != "kEpsilon":
        raise ValueError("epsilon residuals belong to kEpsilon in this comparison")
    if field == "omega" and model != "kOmegaSST":
        raise ValueError("omega residuals belong to kOmegaSST in this comparison")
    return MODEL_COLORS[model]


def residual_control_report(residual_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(residual_rows)
    iterations = [int(float(row["iteration"])) for row in rows if row.get("iteration") not in {None, ""}]
    if not iterations:
        return {
            "final_iteration": None,
            "max_initial_residual_at_final_iteration": None,
            "residual_control_passed": False,
            "max_linear_solver_final_residual": None,
        }
    final_iteration = max(iterations)
    final_rows = [row for row in rows if int(float(row["iteration"])) == final_iteration]
    max_initial = max(float(row["initial_residual"]) for row in final_rows)
    max_linear_final = max(float(row["final_residual"]) for row in final_rows)
    passed = all(float(row["initial_residual"]) <= residual_threshold_for_field(str(row["field"])) for row in final_rows)
    return {
        "final_iteration": final_iteration,
        "max_initial_residual_at_final_iteration": max_initial,
        "residual_control_passed": passed,
        "max_linear_solver_final_residual": max_linear_final,
    }


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _dictionary_block_with_braces(text: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*\{{", text)
    if not match:
        return None
    open_index = text.find("{", match.start())
    close_index = _find_matching_brace(text, open_index)
    return text[match.start() : close_index + 1]


def relaxation_factors_equations_text(fv_solution: Path) -> str:
    text = fv_solution.read_text(encoding="utf-8", errors="ignore")
    relaxation_block = _dictionary_block_with_braces(text, "relaxationFactors")
    if relaxation_block is None:
        return ""
    equations_block = _dictionary_block_with_braces(relaxation_block, "equations")
    return equations_block or relaxation_block


def relaxation_entries_from_text(text: str) -> dict[str, Any]:
    u_match = re.search(r"(?m)^\s*U\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*;", text)
    catch_all_match = re.search(
        r'(?m)^\s*"\.\*"\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*;',
        text,
    )
    return {
        "U_exact_entry": float(u_match.group(1)) if u_match else None,
        "equation_catch_all_regex": ".*" if catch_all_match else None,
        "equation_catch_all_value": float(catch_all_match.group(1)) if catch_all_match else None,
    }


def relaxation_profile_from_cases(model_cases: dict[str, Path]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    profile_entries: dict[str, Any] | None = None
    for model, case_dir in model_cases.items():
        fv_solution = case_dir / "system/fvSolution"
        equations_text = relaxation_factors_equations_text(fv_solution)
        entries = relaxation_entries_from_text(equations_text)
        if profile_entries is None:
            profile_entries = entries
        models[model] = {
            "fvSolution": fv_solution.as_posix(),
            "fvSolution_sha256": sha256(fv_solution) if fv_solution.exists() else None,
            "relaxationFactors_equations": equations_text,
            **entries,
        }
    return {"profile_entries": profile_entries or {}, "models": models}


def parse_internal_field_values(path: Path) -> list[float]:
    text = path.read_text(errors="ignore")
    internal_match = re.search(r"internalField\s+(.*?);", text, flags=re.DOTALL)
    if not internal_match:
        return []
    block = internal_match.group(1)
    values = _parse_nonuniform_values(block)
    if values is None:
        if "uniform" in block:
            parsed = _parse_uniform_value_from_line(block, None)
            values = [parsed]
        else:
            return []
    flattened: list[float] = []
    for value in values:
        if isinstance(value, tuple):
            flattened.extend(float(component) for component in value)
        else:
            flattened.append(float(value))
    return flattened


def field_relative_l2_change(final_path: Path, previous_path: Path) -> dict[str, Any]:
    if not final_path.exists() or not previous_path.exists():
        return {"available": False, "relative_l2": None, "reason": "missing_field_file"}
    final_values = parse_internal_field_values(final_path)
    previous_values = parse_internal_field_values(previous_path)
    if not final_values or len(final_values) != len(previous_values):
        return {"available": False, "relative_l2": None, "reason": "missing_or_mismatched_values"}
    diff_norm = math.sqrt(sum((a - b) ** 2 for a, b in zip(final_values, previous_values)))
    final_norm = math.sqrt(sum(a * a for a in final_values))
    return {"available": True, "relative_l2": diff_norm / max(final_norm, 1e-30), "reason": None}


def field_stability_rows(case_dir: Path, model: str, final_time: str) -> list[dict[str, Any]]:
    final_value = float(final_time)
    available_times: list[tuple[float, str]] = []
    for path in case_dir.iterdir():
        if path.is_dir():
            try:
                available_times.append((float(path.name), path.name))
            except ValueError:
                pass
    available_times = sorted(available_times)
    fields = ["U", "p", "k", "nut"]
    if model == "kEpsilon":
        fields.append("epsilon")
    else:
        fields.append("omega")
    rows = []
    for requested_offset in [100, 500]:
        target = final_value - requested_offset
        candidates = [item for item in available_times if item[1] != final_time]
        comparison = min(candidates, key=lambda item: abs(item[0] - target)) if candidates else None
        comparison_iteration = comparison[0] if comparison else None
        comparison_time = comparison[1] if comparison else None
        actual_offset = final_value - comparison_iteration if comparison_iteration is not None else None
        for field in fields:
            if comparison_time is None:
                report = {"available": False, "relative_l2": None, "reason": "missing_comparison_time"}
            else:
                report = field_relative_l2_change(case_dir / final_time / field, case_dir / comparison_time / field)
            rows.append(
                {
                    "model": model,
                    "field": field,
                    "final_time": final_time,
                    "requested_offset_iterations": requested_offset,
                    "target_iteration": target,
                    "comparison_time": comparison_time,
                    "comparison_iteration": comparison_iteration,
                    "actual_offset_iterations": actual_offset,
                    "available": report["available"],
                    "relative_l2": report["relative_l2"],
                    "reason": report["reason"],
                }
            )
    return rows


def failure_reasons(summary: dict[str, Any], solver_exit_code: int) -> list[str]:
    reasons: list[str] = []
    if solver_exit_code != 0:
        reasons.append("nonzero_solver_exit")
    for key, name in [
        ("has_nan", "nan"),
        ("has_floating_point_exception", "floating_point_exception"),
        ("has_fatal_error", "fatal_error"),
        ("has_unknown_model", "unknown_model"),
        ("has_missing_field", "missing_field"),
    ]:
        if summary.get(key):
            reasons.append(name)
    if not summary.get("ended", False):
        reasons.append("missing_end")
    return reasons


def required_quality_gate_report(model_cases: dict[str, Path]) -> dict[str, Any]:
    required_gates = [
        "blockMesh",
        "checkMesh",
        "pair_audit",
        "simpleFoam",
        "no_solver_failures",
        "final_fields",
        "residual_parser",
        "flow_balance",
        "yPlus",
        "wallShearStress",
        "patch_pressure",
        "simple_solution_converged",
    ]
    model_reports: dict[str, Any] = {}
    all_passed = True
    for model, case_dir in model_cases.items():
        summary_path = case_dir / "results/solver_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        gates = summary.get("quality_gates", {})
        gate_status = {gate: bool(gates.get(gate)) for gate in required_gates}
        no_failure_flags = not any(
            bool(summary.get(key))
            for key in [
                "has_nan",
                "has_floating_point_exception",
                "has_fatal_error",
                "has_unknown_model",
                "has_missing_field",
            ]
        )
        passed = (
            summary.get("status") == "converged"
            and summary.get("quality_gate_status") == "passed"
            and all(gate_status.values())
            and no_failure_flags
        )
        all_passed = all_passed and passed
        model_reports[model] = {
            "status": summary.get("status"),
            "quality_gate_status": summary.get("quality_gate_status"),
            "pair_audit_passed": gate_status["pair_audit"],
            "flow_balance_gate_passed": gate_status["flow_balance"],
            "yPlus_success": gate_status["yPlus"],
            "wallShearStress_success": gate_status["wallShearStress"],
            "patch_pressure_success": gate_status["patch_pressure"],
            "no_fatal_nan_missing_field": no_failure_flags,
            "quality_gates": gate_status,
            "formal_comparison_ready": passed,
        }
    return {
        "comparison_status": "formal_comparison" if all_passed else "quality_incomplete_comparison",
        "models": model_reports,
    }


def update_manifest_relaxation_metadata(case_dir: Path, relaxation_entries: dict[str, Any]) -> None:
    manifest_path = case_dir / "case_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["relaxation"] = relaxation_entries
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def regenerate_case_field_stability(case_dir: Path, model: str, result_status: str | None = None) -> list[dict[str, Any]]:
    final_time = latest_numeric_time(case_dir)
    rows = field_stability_rows(case_dir, model, final_time) if final_time else []
    summary_path = case_dir / "results/solver_summary.json"
    display_model = model
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        display_model = summary.get("display_model") or model_display_name(model, summary.get("status"))
        result_status = result_status or summary.get("result_status") or result_status_for_model(summary.get("status"))
    for row in rows:
        row["display_model"] = display_model
        row["result_status"] = result_status
    write_csv(case_dir / "results/field_stability_summary.csv", rows, FIELD_STABILITY_FIELDS)
    return rows


FIELD_STABILITY_FIELDS = [
    "model",
    "display_model",
    "result_status",
    "field",
    "final_time",
    "requested_offset_iterations",
    "target_iteration",
    "comparison_time",
    "comparison_iteration",
    "actual_offset_iterations",
    "available",
    "relative_l2",
    "reason",
]


def _json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _csv_first(rows: list[dict[str, Any]], **criteria: str) -> dict[str, Any]:
    for row in rows:
        if all(row.get(key) == value for key, value in criteria.items()):
            return row
    return {}


def _as_float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def update_profile_manifest(path: Path, relaxation_by_model: dict[str, dict[str, Any]]) -> None:
    rows = read_csv_rows(path)
    if not rows:
        return
    for row in rows:
        entries = relaxation_by_model.get(row.get("model", ""), {})
        row["U_exact_entry"] = entries.get("U_exact_entry")
        row["equation_catch_all_regex"] = entries.get("equation_catch_all_regex")
        row["equation_catch_all_value"] = entries.get("equation_catch_all_value")
    fieldnames = list(rows[0].keys())
    for name in ["U_exact_entry", "equation_catch_all_regex", "equation_catch_all_value"]:
        if name not in fieldnames:
            fieldnames.append(name)
    write_csv(path, rows, fieldnames)


def _model_cases_for_profile(output_root: Path, profile_name: str) -> dict[str, Path]:
    profile_root = output_root / profile_name
    return {model: profile_root / model for model in MODELS}


def _all_profile_dirs(output_root: Path) -> list[Path]:
    return [
        path
        for path in sorted(output_root.iterdir())
        if path.is_dir() and all((path / model / "system/fvSolution").exists() for model in MODELS)
    ]


def _update_run_relaxation_metadata(output_root: Path) -> dict[str, Any]:
    profile_reports: dict[str, Any] = {}
    for profile_root in _all_profile_dirs(output_root):
        model_cases = {model: profile_root / model for model in MODELS}
        report = relaxation_profile_from_cases(model_cases)
        profile_reports[profile_root.name] = report
        entries_by_model = {
            model: {
                "U_exact_entry": model_report.get("U_exact_entry"),
                "equation_catch_all_regex": model_report.get("equation_catch_all_regex"),
                "equation_catch_all_value": model_report.get("equation_catch_all_value"),
            }
            for model, model_report in report["models"].items()
        }
        for model, case_dir in model_cases.items():
            entries = entries_by_model[model]
            update_manifest_relaxation_metadata(case_dir, entries)
            summary_path = case_dir / "results/solver_summary.json"
            if summary_path.exists():
                summary = _json_file(summary_path)
                summary["relaxation"] = entries
                summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        update_profile_manifest(profile_root / "manifest.csv", entries_by_model)
    return profile_reports


def _combine_field_stability(output_root: Path, selected_profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in _model_cases_for_profile(output_root, selected_profile).items():
        rows.extend(regenerate_case_field_stability(case_dir, model))
    write_csv(output_root / "comparison/field_stability_summary.csv", rows, FIELD_STABILITY_FIELDS)
    return rows


def _residual_rows_by_model(model_cases: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in model_cases.items():
        summary = _json_file(case_dir / "results/solver_summary.json")
        display_model = summary.get("display_model") or model_display_name(model, summary.get("status"))
        for row in read_csv_rows(case_dir / "results/residuals.csv"):
            rows.append({"model": model, "display_model": display_model, **row})
    return rows


def write_residual_control_figures(model_cases: dict[str, Path], figure_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure_dir.mkdir(parents=True, exist_ok=True)
    rows = _residual_rows_by_model(model_cases)
    if not rows:
        return []

    field_order = ["Ux", "Uy", "p", "k", "epsilon", "omega"]
    fields = [field for field in field_order if any(row["field"] == field for row in rows)]
    model_labels = {
        row["model"]: row.get("display_model") or row["model"]
        for row in rows
    }

    fig, axes = plt.subplots(len(fields), 1, figsize=(8, max(2.0 * len(fields), 4.0)), sharex=True)
    if len(fields) == 1:
        axes = [axes]
    for ax, field in zip(axes, fields):
        for model in MODELS:
            model_rows = [row for row in rows if row["model"] == model and row["field"] == field]
            if not model_rows:
                continue
            xs = [int(float(row["iteration"])) for row in model_rows]
            ys = [float(row["initial_residual"]) for row in model_rows]
            ax.semilogy(
                xs,
                ys,
                label=model_labels.get(model, model),
                color=residual_color_for_model_field(model, field),
            )
        ax.axhline(residual_threshold_for_field(field), color="black", linestyle="--", linewidth=0.8)
        ax.set_ylabel(field)
        ax.grid(True, which="both", alpha=0.3)
    axes[-1].set_xlabel("Iteration")
    axes[0].legend()
    fig.suptitle("Initial Residual History by Field")
    fig.tight_layout()
    by_field_path = figure_dir / "residual_by_field_comparison.png"
    fig.savefig(by_field_path, dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    for model in MODELS:
        model_rows = [row for row in rows if row["model"] == model]
        by_iteration: dict[int, float] = {}
        for row in model_rows:
            iteration = int(float(row["iteration"]))
            threshold = residual_threshold_for_field(row["field"])
            ratio = float(row["initial_residual"]) / threshold
            by_iteration[iteration] = max(by_iteration.get(iteration, 0.0), ratio)
        if by_iteration:
            xs = sorted(by_iteration)
            ax.semilogy(
                xs,
                [by_iteration[x] for x in xs],
                label=model_labels.get(model, model),
                color=MODEL_COLORS[model],
            )
    ax.axhline(1.0, color="black", linestyle="--", linewidth=0.9, label="threshold ratio = 1")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Max initial residual / field threshold")
    ax.set_title("Normalized Residual-Control Diagnostic")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    normalized_path = figure_dir / "normalized_residual_control.png"
    fig.savefig(normalized_path, dpi=160)
    plt.close(fig)

    stale = figure_dir / "residual_history_comparison.png"
    if stale.exists():
        stale.unlink()
    return [by_field_path.as_posix(), normalized_path.as_posix()]


def _selected_model_summary_rows(output_root: Path, selected_profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in _model_cases_for_profile(output_root, selected_profile).items():
        summary = _json_file(case_dir / "results/solver_summary.json")
        yplus_rows = read_csv_rows(case_dir / "results/yplus_summary.csv")
        pressure_rows = read_csv_rows(case_dir / "results/patch_pressure_summary.csv")
        wall_shear_rows = read_csv_rows(case_dir / "results/wall_shear_stress_values.csv")
        reattachment = _json_file(case_dir / "results/reattachment_summary.json")
        lower_y = _csv_first(yplus_rows, patch="lowerWall")
        upper_y = _csv_first(yplus_rows, patch="upperWall")
        pressure = pressure_rows[0] if pressure_rows else {}
        wall_dimensions = wall_shear_rows[0].get("field_dimensions") if wall_shear_rows else None
        rows.append(
            {
                "model": model,
                "status": summary.get("status"),
                "quality_gate_status": summary.get("quality_gate_status"),
                "actual_iterations": summary.get("actual_iterations"),
                "wall_clock_seconds": summary.get("wall_clock_seconds"),
                "max_initial_residual_at_final_iteration": summary.get("max_initial_residual_at_final_iteration"),
                "residual_control_passed": summary.get("residual_control_passed"),
                "relative_flow_imbalance": summary.get("relative_flow_imbalance"),
                "pressure_recovery_kinematic": pressure.get("pressure_recovery_kinematic")
                or summary.get("pressure_recovery_kinematic"),
                "lowerWall_yplus_median": lower_y.get("median"),
                "lowerWall_yplus_p95": lower_y.get("p95"),
                "upperWall_yplus_median": upper_y.get("median"),
                "upperWall_yplus_p95": upper_y.get("p95"),
                "wallShearStress_dimensions": wall_dimensions,
                "reattachment_status": reattachment.get("status"),
                "attached_sign": reattachment.get("attached_sign"),
                "upstream_attached_median": reattachment.get("upstream_attached_median"),
                "x_reattachment_raw": reattachment.get("x_reattachment_raw"),
                "reattachment_length_normalized": reattachment.get("reattachment_length_normalized"),
                "confidence_status": reattachment.get("confidence_status"),
            }
        )
    return rows


def _selected_quality_gate_rows(output_root: Path, selected_profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    report = required_quality_gate_report(_model_cases_for_profile(output_root, selected_profile))
    for model, model_report in report["models"].items():
        for gate, passed in model_report["quality_gates"].items():
            rows.append(
                {
                    "model": model,
                    "gate": gate,
                    "passed": passed,
                    "quality_gate_status": model_report["quality_gate_status"],
                    "comparison_status": report["comparison_status"],
                }
            )
    return rows


def _selected_reattachment_rows(output_root: Path, selected_profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, case_dir in _model_cases_for_profile(output_root, selected_profile).items():
        row = _json_file(case_dir / "results/reattachment_summary.json")
        if not row:
            continue
        selected = row.get("selected_crossing")
        rows.append(
            {
                "model": model,
                "status": row.get("status"),
                "result_status": row.get("result_status"),
                "attached_sign": row.get("attached_sign"),
                "separated_sign": row.get("separated_sign"),
                "upstream_attached_median": row.get("upstream_attached_median"),
                "x_reattachment_raw": row.get("x_reattachment_raw"),
                "reattachment_length_normalized": row.get("reattachment_length_normalized"),
                "confidence_status": row.get("confidence_status"),
                "selected_reason": row.get("confidence_status"),
                "selected_crossing_json": json.dumps(selected, sort_keys=True),
                "all_crossings_json": json.dumps(row.get("all_crossings", []), sort_keys=True),
            }
        )
    return rows


def _legacy_reattachment_suspicion(model_summary_rows: list[dict[str, Any]]) -> dict[str, Any]:
    legacy = {"kEpsilon": 0.187, "kOmegaSST": 0.0199}
    rows: dict[str, Any] = {}
    suspicious = False
    for row in model_summary_rows:
        model = str(row["model"])
        current = _as_float_or_none(row.get("reattachment_length_normalized"))
        target = legacy.get(model)
        close = current is not None and target is not None and math.isclose(current, target, rel_tol=0.05, abs_tol=0.05)
        suspicious = suspicious or close
        rows[model] = {
            "current_reattachment_length_normalized": current,
            "legacy_diagnostic_value": target,
            "suspicious": close,
        }
    return {"suspicious": suspicious, "models": rows}


def generate_final_audit(output_root: Path, selected_profile: str | None = None) -> dict[str, Any]:
    selection_path = output_root / "selected/selection_summary.json"
    selection = _json_file(selection_path)
    selected_profile = selected_profile or selection.get("selected_profile") or "conservative_common"
    model_cases = _model_cases_for_profile(output_root, selected_profile)
    final_dir = output_root / "final_audit"
    final_dir.mkdir(parents=True, exist_ok=True)

    all_relaxation = _update_run_relaxation_metadata(output_root)
    selected_relaxation = all_relaxation.get(selected_profile) or relaxation_profile_from_cases(model_cases)
    relaxation_path = final_dir / "relaxation_profile.json"
    relaxation_path.write_text(json.dumps(selected_relaxation, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stability_rows = _combine_field_stability(output_root, selected_profile)
    figure_paths = write_residual_control_figures(model_cases, output_root / "comparison/figures")

    comparison_report = required_quality_gate_report(model_cases)
    selection.update(
        {
            "selected_profile": selected_profile,
            "selection_reason": "first common profile for which both models converged and passed required quality gates",
            "selection_basis": "convergence and required quality gates only",
            "comparison_status": comparison_report["comparison_status"],
            "relaxation": selected_relaxation.get("profile_entries", {}),
        }
    )
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    model_rows = _selected_model_summary_rows(output_root, selected_profile)
    quality_rows = _selected_quality_gate_rows(output_root, selected_profile)
    reattachment_rows = _selected_reattachment_rows(output_root, selected_profile)
    write_csv(final_dir / "selected_model_summary.csv", model_rows)
    write_csv(final_dir / "selected_quality_gates.csv", quality_rows)
    write_csv(final_dir / "selected_field_stability.csv", stability_rows, FIELD_STABILITY_FIELDS)
    write_csv(final_dir / "selected_reattachment.csv", reattachment_rows)

    reattachment_details = {
        model: _json_file(case_dir / "results/reattachment_summary.json")
        for model, case_dir in model_cases.items()
    }
    final_audit = {
        "selected_profile": selected_profile,
        "selection_reason": selection["selection_reason"],
        "comparison_status": comparison_report["comparison_status"],
        "comparison_gate_report": comparison_report,
        "output_files": {
            "selected_model_summary": (final_dir / "selected_model_summary.csv").as_posix(),
            "selected_quality_gates": (final_dir / "selected_quality_gates.csv").as_posix(),
            "selected_field_stability": (final_dir / "selected_field_stability.csv").as_posix(),
            "selected_reattachment": (final_dir / "selected_reattachment.csv").as_posix(),
            "relaxation_profile": relaxation_path.as_posix(),
        },
        "relaxation_profile_path": relaxation_path.as_posix(),
        "relaxation": selected_relaxation,
        "model_summary": model_rows,
        "quality_gate_row_count": len(quality_rows),
        "field_stability_row_count": len(stability_rows),
        "reattachment": reattachment_details,
        "reattachment_algorithm_evidence": {
            "upstream_attached_sign_calibration": True,
            "sustained_separated_region_required": True,
            "final_stable_recovery_selection": True,
            "local_downstream_tangent_used": True,
            "fixed_negative_to_positive_rule_used": False,
        },
        "legacy_reattachment_suspicion": _legacy_reattachment_suspicion(model_rows),
        "residual_figures": figure_paths,
    }
    (final_dir / "final_audit.json").write_text(
        json.dumps(final_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return final_audit


def postprocess_case(
    case_dir: Path,
    model: str,
    wall_clock_seconds: float,
    solver_exit_code: int = 0,
    pair_audit_passed: bool = True,
    run_start_epoch: int | None = None,
    run_end_epoch: int | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    result_dir = case_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    summary_path = result_dir / "solver_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    status = classify_solver_status(summary, solver_exit_code)
    result_status = result_status_for_model(status)
    display_model = model_display_name(model, status)
    final_time = latest_numeric_time(case_dir)
    residual_report = residual_control_report(read_csv_rows(result_dir / "residuals.csv"))

    flow_rows: list[dict[str, Any]] = []
    pressure_row: dict[str, Any] | None = None
    yplus_values: list[dict[str, Any]] = []
    yplus_summary_rows: list[dict[str, Any]] = []
    wall_shear_rows: list[dict[str, Any]] = []
    reattachment: dict[str, Any] = {"status": "not_detected"}
    reverse_flow: dict[str, Any] = {"model": model, "status": "not_run", "reason": "no final numeric time"}
    stability_rows: list[dict[str, Any]] = []
    relative_imbalance = None
    quality_status = "failed"

    if final_time:
        flow_rows, relative_imbalance = collect_patch_flow_rates(case_dir, model, final_time)
        pressure_row = collect_patch_pressure(case_dir, model, final_time)
        yplus_values, yplus_summary_rows = collect_yplus(case_dir, model, final_time)
        wall_shear_rows = collect_wall_shear(case_dir, model, final_time)
        reattachment = collect_reattachment(case_dir, wall_shear_rows)
        reverse_flow = reverse_flow_summary(case_dir, model, final_time)
        stability_rows = field_stability_rows(case_dir, model, final_time)

    for rows in [flow_rows, yplus_values, yplus_summary_rows, wall_shear_rows, stability_rows]:
        for row in rows:
            row["display_model"] = display_model
            row["result_status"] = result_status
    if pressure_row:
        pressure_row["display_model"] = display_model
        pressure_row["result_status"] = result_status

    write_csv(result_dir / "patch_flow_rates.csv", flow_rows, [
        "model",
        "display_model",
        "result_status",
        "patch",
        "signed_volumetric_flow_rate",
        "absolute_volumetric_flow_rate",
        "field_dimensions",
        "final_time",
    ])
    write_csv(result_dir / "patch_pressure_summary.csv", [pressure_row] if pressure_row else [], [
        "model",
        "display_model",
        "result_status",
        "p_inlet_area_average",
        "p_outlet_area_average",
        "delta_p_in_minus_out_kinematic",
        "pressure_recovery_kinematic",
        "delta_p_kinematic",
        "field_dimensions",
    ])
    write_csv(result_dir / "yplus_patch_values.csv", yplus_values, [
        "model",
        "display_model",
        "result_status",
        "patch",
        "face_index",
        "yplus",
        "field_dimensions",
    ])
    write_csv(result_dir / "yplus_summary.csv", yplus_summary_rows, [
        "model",
        "display_model",
        "result_status",
        "patch",
        "count",
        "min",
        "median",
        "mean",
        "p05",
        "p95",
        "max",
        "finite_fraction",
        "field_dimensions",
    ])
    write_csv(result_dir / "wall_shear_stress_values.csv", wall_shear_rows, [
        "model",
        "display_model",
        "result_status",
        "patch",
        "face_index",
        "x",
        "y",
        "z",
        "tau_x",
        "tau_y",
        "tau_z",
        "tau_streamwise",
        "tau_global_streamwise",
        "local_wall_tangent_x",
        "local_wall_tangent_y",
        "local_wall_tangent_z",
        "tau_downstream_tangent",
        "tau_tangent_minus_global_streamwise",
        "field_dimensions",
    ])
    write_csv(result_dir / "field_stability_summary.csv", stability_rows, FIELD_STABILITY_FIELDS)
    (result_dir / "geometry_audit.json").write_text(
        json.dumps(reattachment.get("geometry", _empty_geometry_audit()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reattachment_for_json = {key: value for key, value in reattachment.items() if key != "geometry"}
    (result_dir / "reattachment_summary.json").write_text(
        json.dumps(
            {
                "model": model,
                "display_model": display_model,
                "result_status": result_status,
                **reattachment_for_json,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (result_dir / "reverse_flow_summary.json").write_text(
        json.dumps(reverse_flow, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    gates = {
        "blockMesh": (case_dir / "logs/blockMesh.log").exists(),
        "checkMesh": "Mesh OK" in (case_dir / "logs/checkMesh.log").read_text(errors="ignore")
        if (case_dir / "logs/checkMesh.log").exists()
        else False,
        "pair_audit": pair_audit_passed,
        "simpleFoam": solver_exit_code == 0,
        "no_solver_failures": not bool(failure_reasons(summary, solver_exit_code)),
        "final_fields": bool(final_time and (case_dir / final_time / "U").exists() and (case_dir / final_time / "p").exists()),
        "residual_parser": (result_dir / "residuals.csv").exists() and bool(summary),
        "flow_balance": relative_imbalance is not None and relative_imbalance <= 0.01,
        "yPlus": bool(yplus_summary_rows),
        "wallShearStress": bool(wall_shear_rows),
        "patch_pressure": pressure_row is not None
        and pressure_row.get("delta_p_in_minus_out_kinematic") is not None,
        "simple_solution_converged": status == "converged",
    }
    quality_status = quality_gate_status(gates)

    summary.update(
        {
            "model": model,
            "display_model": display_model,
            "profile_name": profile_name,
            "status": status,
            "result_status": result_status,
            "actual_iterations": summary.get("actual_iterations"),
            "simple_solution_converged": bool(summary.get("converged")),
            "max_initial_residual_at_final_iteration": residual_report.get("max_initial_residual_at_final_iteration"),
            "residual_control_passed": residual_report.get("residual_control_passed"),
            "max_linear_solver_final_residual": residual_report.get("max_linear_solver_final_residual"),
            "max_final_residual": max_final_residual(summary.get("final_residuals", {})),
            "failure_reasons": failure_reasons(summary, solver_exit_code),
            "run_start_epoch": run_start_epoch,
            "run_end_epoch": run_end_epoch,
            "wall_clock_seconds": wall_clock_seconds,
            "final_time": final_time,
            "relative_flow_imbalance": relative_imbalance,
            "flow_balance_gate_passed": relative_imbalance is not None and relative_imbalance <= 0.01,
            "delta_p_in_minus_out_kinematic": pressure_row.get("delta_p_in_minus_out_kinematic")
            if pressure_row
            else None,
            "pressure_recovery_kinematic": pressure_row.get("pressure_recovery_kinematic") if pressure_row else None,
            "delta_p_kinematic": pressure_row.get("delta_p_kinematic") if pressure_row else None,
            "quality_gate_status": quality_status,
            "quality_gates": gates,
            "reattachment_status": reattachment_for_json.get("status"),
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def openfoam_version() -> str | None:
    return os.environ.get("WM_PROJECT_VERSION")


def run_command(command: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return completed.returncode, completed.stdout, completed.stderr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-setup", help="Write inlet turbulence and boundary-condition audits.")
    audit.add_argument("--k-epsilon", type=Path, required=True)
    audit.add_argument("--k-omega-sst", type=Path, required=True)
    audit.add_argument("--output-dir", type=Path, required=True)

    capabilities = subparsers.add_parser("audit-postprocess", help="Write local post-processing capability audit.")
    capabilities.add_argument("--case-dir", type=Path)
    capabilities.add_argument("--output", type=Path, required=True)

    post = subparsers.add_parser("postprocess-case", help="Collect formal post-processing metrics for one case.")
    post.add_argument("--case-dir", type=Path, required=True)
    post.add_argument("--model", choices=MODELS, required=True)
    post.add_argument("--wall-clock-seconds", type=float, required=True)
    post.add_argument("--solver-exit-code", type=int, default=0)
    post.add_argument("--pair-audit-passed", action="store_true")
    post.add_argument("--run-start-epoch", type=int)
    post.add_argument("--run-end-epoch", type=int)
    post.add_argument("--profile-name")

    freeze = subparsers.add_parser("freeze-v1", help="Write SHA256 audit records for an existing v1 run.")
    freeze.add_argument("--v1-root", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    final = subparsers.add_parser("final-audit", help="Regenerate final v2 audit reports from existing solver outputs.")
    final.add_argument("--output-root", type=Path, required=True)
    final.add_argument("--selected-profile")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "audit-setup":
        report, rows = audit_turbulence_initialization(args.k_epsilon, args.k_omega_sst)
        write_turbulence_audit(args.output_dir, report, rows)
        print(json.dumps(report, indent=2, sort_keys=True, default=_jsonable))
        return 0 if report["gate_passed"] else 1
    if args.command == "audit-postprocess":
        report = discover_postprocess_capabilities(args.case_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "postprocess-case":
        summary = postprocess_case(
            args.case_dir,
            args.model,
            args.wall_clock_seconds,
            args.solver_exit_code,
            args.pair_audit_passed,
            args.run_start_epoch,
            args.run_end_epoch,
            args.profile_name,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary.get("status") != "failed" else 1
    if args.command == "freeze-v1":
        report = write_v1_hashes(args.v1_root, args.output)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "final-audit":
        report = generate_final_audit(args.output_root, args.selected_profile)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["legacy_reattachment_suspicion"]["suspicious"] else 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
