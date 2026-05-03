"""
CadQuery / PythonOCC backend — executes Python scripts using CadQuery.
Scripts should use `import cadquery as cq` and end with:
    cq.exporters.export(result, __output_stl__)
"""

import subprocess
import tempfile
from pathlib import Path

TEMPLATE = """
import cadquery as cq
import sys

__output_stl__ = "{stl_path}"
__output_step__ = "{step_path}"

# --- begin user script ---
{user_code}
# --- end user script ---
"""


def render(script_path: Path, output_stl: Path, output_step: Path | None = None) -> tuple[bool, str]:
    """Run a CadQuery script, produce STL (and optionally STEP)."""
    output_stl.parent.mkdir(parents=True, exist_ok=True)
    step = output_step or output_stl.with_suffix(".step")
    step.parent.mkdir(parents=True, exist_ok=True)

    user_code = script_path.read_text()
    wrapped = TEMPLATE.format(
        stl_path=str(output_stl),
        step_path=str(step),
        user_code=user_code,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(wrapped)
        wrapper_path = f.name

    try:
        result = subprocess.run(
            ["python3", wrapper_path],
            capture_output=True, text=True, timeout=120,
        )
        success = result.returncode == 0 and output_stl.exists()
        return success, result.stderr
    finally:
        Path(wrapper_path).unlink(missing_ok=True)
