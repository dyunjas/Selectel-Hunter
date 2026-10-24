"""python -m app.healthcheck; checks local watchdog health without API calls."""
import json
import os
import sys
import time
from pathlib import Path


def main():
    try:
        data = json.loads(Path(os.getenv("HEALTH_FILE", "health.json")).read_text(encoding="utf-8"))
        return 0 if data["ok"] and time.time() - data["at"] < 90 else 1
    except (OSError, ValueError, KeyError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
