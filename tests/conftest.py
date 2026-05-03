from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_LIB = ROOT / "build" / "lib"


def _resolved_text(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


root_text = _resolved_text(ROOT)
build_lib_text = _resolved_text(BUILD_LIB)
filtered_path = [
    entry
    for entry in sys.path
    if _resolved_text(entry or ".") not in {root_text, build_lib_text}
]
sys.path[:] = [str(ROOT), *filtered_path]

# Preload the repo-local packages so pytest cannot satisfy imports from stale build outputs.
import execution.executor  # noqa: E402, F401 — intentionally after sys.path rewrite; imported for side-effects
import memory.database  # noqa: E402, F401
import orchestration.workflow  # noqa: E402, F401
