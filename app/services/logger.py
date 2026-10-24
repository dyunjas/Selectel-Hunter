import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler


def configure_logging(level: str = "INFO") -> None:
    path = Path(os.getenv("LOG_FILE", "logs/hunter.log"))
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")])
