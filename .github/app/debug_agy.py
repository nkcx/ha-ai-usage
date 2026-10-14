"""Replay the app's first Gemini collection inside the container and report."""

import asyncio
import os
from pathlib import Path
import time

from ai_usage_app.installer import Installer
from ai_usage_app.settings import Paths
from ai_usage_core.runner import SubprocessRunner
from ai_usage_core.sources.gemini import GeminiSource


async def main() -> None:
    """Install agy into a fresh /data, collect right away, then again."""
    paths = Paths(Path("/tmp/debug-data"))
    paths.ensure()
    runner = SubprocessRunner(paths.env(), cwd=str(paths.work))
    start = time.monotonic()
    await Installer(paths, runner).ensure(("gemini",))
    print(f"installed after {time.monotonic() - start:.1f}s")
    print("PROCESSES after install:", os.popen("ps -o pid,pgid,stat,args").read())
    for attempt in (1, 2):
        start = time.monotonic()
        report = await GeminiSource(runner).collect()
        print(
            f"collect {attempt}: {report.status} after {time.monotonic() - start:.1f}s:"
            f" {report.error}"
        )
    print("PROCESSES after collect:", os.popen("ps -o pid,pgid,stat,args").read())


asyncio.run(main())
