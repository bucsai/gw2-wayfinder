import os
from pathlib import Path

MODEL = os.environ.get("WAYFINDER_MODEL", "anthropic:claude-haiku-5-5")
DATA_DIR = Path(os.environ.get("WAYFINDER_DATA_DIR", "data"))
USER_AGENT = "GW2Wayfinder/0.1 (+https://gw2wayfinder.com; https://github.com/bucsai/gw2-wayfinder)"
