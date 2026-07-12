"""
logging_setup.py
================
One shared logger, writing to both console and a per-run log file under
work/logs/. Every stage in the pipeline logs its progress here so a long
run (large PDFs, many pages) leaves a readable trail you can inspect or
grep after the fact, and so failures are traceable to a specific page/stage.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(work_dir: Path, name: str = "elluval") -> logging.Logger:
    log_dir = work_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.info("Logging to %s", log_file)
    return logger
