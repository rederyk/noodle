"""
OpenSCAD backend — renders .scad files via CLI, extracts params.
"""

import re
import subprocess
from pathlib import Path

PARAM_PATTERN = re.compile(r"^//\s*Param:\s*(\w+)\s*=\s*(.+)$", re.MULTILINE)


def extract_params(code: str) -> dict:
    """Extract // Param: name = value annotations from SCAD code."""
    params = {}
    for m in PARAM_PATTERN.finditer(code):
        key, val = m.group(1), m.group(2).strip()
        try:
            val = float(val)
            if val == int(val):
                val = int(val)
        except ValueError:
            pass
        params[key] = val
    return params


def apply_params(code: str, params: dict) -> str:
    """Replace // Param: annotations with new values."""
    for key, val in params.items():
        code = re.sub(
            rf"(//\s*Param:\s*{key}\s*=\s*).+$",
            rf"\g<1>{val}",
            code,
            flags=re.MULTILINE,
        )
    return code


def render(scad_path: Path, output_stl: Path) -> tuple[bool, str]:
    """Render .scad → .stl. Returns (success, stderr)."""
    result = subprocess.run(
        ["openscad", "-o", str(output_stl), str(scad_path)],
        capture_output=True, text=True, timeout=120,
    )
    return result.returncode == 0, result.stderr


def export_step(scad_path: Path, output_path: Path) -> tuple[bool, str]:
    """Export .scad → .step (if supported)."""
    result = subprocess.run(
        ["openscad", "-o", str(output_path), str(scad_path)],
        capture_output=True, text=True, timeout=120,
    )
    return result.returncode == 0, result.stderr
