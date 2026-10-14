"""Copy the shared core into the app (``--check``: fail if the copy is stale).

The integration owns ``custom_components/ai_usage/core``; the app ships an
identical copy because its Docker build only sees the app folder.
"""

import filecmp
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "custom_components" / "ai_usage" / "core"
TARGET = ROOT / "ai_usage_app" / "rootfs" / "opt" / "ai_usage" / "ai_usage_core"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def stale() -> list[str]:
    """Return the files that differ between the core and its copy."""
    comparison = filecmp.dircmp(SOURCE, TARGET, ignore=["__pycache__"])
    found: list[str] = []

    def walk(cmp: filecmp.dircmp[str], prefix: str) -> None:
        found.extend(
            prefix + name for name in cmp.left_only + cmp.right_only + cmp.diff_files
        )
        for name, sub in cmp.subdirs.items():
            walk(sub, f"{prefix}{name}/")

    walk(comparison, "")
    return found


if __name__ == "__main__":
    if "--check" in sys.argv:
        if not TARGET.exists() or (differences := stale()):
            print("The app's copy of the core is stale; run scripts/sync_core.py")
            sys.exit(1)
        print("The app's copy of the core is up to date")
    else:
        shutil.rmtree(TARGET, ignore_errors=True)
        shutil.copytree(SOURCE, TARGET, ignore=IGNORE)
        print(f"Copied {SOURCE.relative_to(ROOT)} -> {TARGET.relative_to(ROOT)}")
