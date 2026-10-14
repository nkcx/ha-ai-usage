"""AI Usage app: reads usage from the official AI CLIs for Home Assistant."""

import os

__version__ = os.environ.get("AI_USAGE_VERSION", "dev")
