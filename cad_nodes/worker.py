"""
Warm execution worker — imports build123d ONCE, then runs transpiled job
scripts on demand in the same interpreter, so each render avoids the ~2.7s
cold-start import (the actual build+tessellate is only 5-165ms).

Protocol: newline-delimited JSON over stdin; responses on stdout, each prefixed
with SENTINEL so build123d / OCCT C++ noise on stdout can't corrupt the channel.
  in : {"cmd":"run","script_path":"/abs/_run.py","cwd":"/abs/workdir"}
       {"cmd":"exit"}
  out: SENTINEL + {"ok":bool, "stdout":str, "error":str}   (one line)
"""

import contextlib
import io
import json
import os
import sys
import traceback

SENTINEL = "@@CADWORKER@@"

# Warm the kernel once: the job scripts do `from build123d import *`, which is
# instant once build123d is in sys.modules.
try:
    import build123d  # noqa: F401
    _IMPORT_ERR = None
except Exception:
    _IMPORT_ERR = traceback.format_exc()


def _send(obj) -> None:
    sys.stdout.write(SENTINEL + json.dumps(obj) + "\n")
    sys.stdout.flush()


def _run(job: dict) -> dict:
    path = job["script_path"]
    cwd = job.get("cwd")
    if cwd:
        try:
            os.chdir(cwd)  # so relative export paths land in the graph's dir
        except Exception:
            pass
    try:
        with open(path) as f:
            src = f.read()
    except Exception:
        return {"ok": False, "stdout": "", "error": traceback.format_exc()}

    ns = {"__name__": "__cad_job__", "__file__": path}
    buf = io.StringIO()
    try:
        code = compile(src, path, "exec")  # filename=path so tracebacks map to _run.py
        with contextlib.redirect_stdout(buf):
            exec(code, ns)
        return {"ok": True, "stdout": buf.getvalue(), "error": ""}
    except Exception:
        return {"ok": False, "stdout": buf.getvalue(), "error": traceback.format_exc()}


def main() -> None:
    if _IMPORT_ERR:
        _send({"ready": False, "error": _IMPORT_ERR})
        return
    _send({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except Exception:
            continue
        if job.get("cmd") == "exit":
            break
        if job.get("cmd") == "run":
            _send(_run(job))


if __name__ == "__main__":
    main()
