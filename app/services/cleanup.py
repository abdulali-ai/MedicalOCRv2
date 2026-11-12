"""Delete stored results, uploads, and page images older than the retention window."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _remove_old(path: Path, cutoff: float) -> int:
    if not path.exists():
        return 0
    removed = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if child.stat().st_mtime < cutoff:
            child.unlink(missing_ok=True)
            removed += 1
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
    return removed


def main() -> None:
    cutoff = time.time() - settings.TASK_RESULT_EXPIRE_TIME
    root = Path(settings.STORAGE_DIR)
    removed = 0
    for name in ("results", "uploads", "pages"):
        removed += _remove_old(root / name, cutoff)
    logger.info("Cleanup removed %s files older than %s seconds", removed, settings.TASK_RESULT_EXPIRE_TIME)


if __name__ == "__main__":
    main()
