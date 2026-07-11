"""Custom-font library — upload .ttf/.otf/.ttc and use them in Text nodes WITHOUT
installing them in the OS.

Fonts live in ONE shared, host-editable directory (``projects/_fonts/``) so they
are reusable across every project. build123d's ``Text`` takes either a system
family name (``font=``, resolved by fontconfig) or a file (``font_path=``). This
module is the single source of truth for both surfaces:

- ``server.py`` lists / stores / deletes fonts for the picker (``/api/fonts``).
- the transpiler PREAMBLE's ``_font(name)`` calls :func:`resolve_font` at run
  time to turn a stored font name into the right ``Text`` kwargs.

A picker value is a FAMILY name (nice to read); :func:`resolve_font` matches it
back to an uploaded file by family / filename stem / filename, and falls back to
``font=<name>`` (a system family) when nothing custom matches.
"""
from __future__ import annotations

import os
from pathlib import Path

FONTS_DIRNAME = "_fonts"                       # reserved dir under projects/
FONT_EXTS = (".ttf", ".otf", ".ttc")
# Where the OS keeps installed fonts — scanned only to SUGGEST family names in
# the picker (they still resolve through fontconfig, not these paths).
_SYSTEM_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts",
                     os.path.expanduser("~/.fonts"))


def default_fonts_dir() -> Path:
    """The shared fonts library dir. Overridable with ``NOODLE_FONTS_DIR`` (tests
    / non-container runs); otherwise ``<repo_root>/projects/_fonts``, resolved
    relative to this file so it is independent of the worker's cwd."""
    env = os.environ.get("NOODLE_FONTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "projects" / FONTS_DIRNAME


def family_of(path) -> str:
    """Best human family name of a font file (fontTools), or its filename stem
    on any error — never raises."""
    stem = Path(path).stem
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(str(path), fontNumber=0, lazy=True)
        try:
            name = f["name"]
            fam = name.getBestFamilyName() or name.getDebugName(1)
        finally:
            f.close()
        return fam or stem
    except Exception:
        return stem


def _iter_font_files(d: Path):
    if not d or not Path(d).is_dir():
        return
    for f in sorted(Path(d).iterdir()):
        if f.is_file() and f.suffix.lower() in FONT_EXTS:
            yield f


def list_custom_fonts(fonts_dir=None) -> list[dict]:
    """Uploaded fonts: ``{name(=family), family, file, ext, size, mtime}`` — for
    the Text picker and the /library management panel."""
    d = Path(fonts_dir) if fonts_dir else default_fonts_dir()
    out = []
    for f in _iter_font_files(d):
        fam = family_of(f)
        try:
            st = f.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0
        out.append({"name": fam, "family": fam, "file": f.name,
                    "ext": f.suffix.lower(), "custom": True,
                    "size": size, "mtime": mtime})
    return out


def list_system_families(limit: int = 400) -> list[str]:
    """Distinct family names of OS-installed fonts (suggestions for the picker).
    fontconfig still does the actual resolution at render time."""
    fams: set[str] = set()
    for d in _SYSTEM_FONT_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in FONT_EXTS:
                fams.add(family_of(f))
                if len(fams) >= limit:
                    break
    return sorted(fams)


def list_fonts(fonts_dir=None) -> dict:
    """Everything the picker needs: custom (uploaded) + system families."""
    return {"custom": list_custom_fonts(fonts_dir),
            "system": list_system_families()}


def resolve_font(name, fonts_dir=None) -> dict:
    """Map a stored font name to ``Text`` kwargs. A custom uploaded font (matched
    by family / filename stem / filename) -> ``{"font_path": <abs path>}``; any
    other name -> ``{"font": <name>}`` (a system family via fontconfig)."""
    if not name:
        return {"font": "Arial"}
    d = Path(fonts_dir) if fonts_dir else default_fonts_dir()
    low = str(name).lower()
    for f in _iter_font_files(d):
        stem = f.stem.lower()
        if low in (f.name.lower(), stem, family_of(f).lower()):
            return {"font_path": str(f)}
    return {"font": str(name)}
