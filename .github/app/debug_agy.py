"""Run agy /usage with the app's runner inside the container and report details."""

import asyncio
import os
import time

from ai_usage_core.runner import SubprocessRunner


async def main() -> None:
    """Print timing, abort state and output of one agy /usage run."""
    runner = SubprocessRunner(cwd="/data/work")
    start = time.monotonic()
    try:
        result = await runner.run(
            ["agy", "-p", "/usage", "--output-format", "json"],
            time_limit=90,
            abort_on=("Authentication required",),
        )
    except TimeoutError as err:
        print(f"TIMEOUT after {time.monotonic() - start:.1f}s")
        print("STDOUT:", getattr(err, "stdout", ""))
        print("STDERR:", getattr(err, "stderr", ""))
        return
    print(
        f"rc={result.returncode} aborted={result.aborted} "
        f"after {time.monotonic() - start:.1f}s"
    )
    print("STDOUT:", result.stdout[:600])
    print("STDERR:", result.stderr[:600])
    print("PROCESSES:", os.popen("ps -o pid,pgid,args").read())


asyncio.run(main())
