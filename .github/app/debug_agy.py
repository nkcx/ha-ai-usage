"""Run agy /usage with the app's runner inside the container and report details."""

import asyncio
import os
import time

from ai_usage_core.runner import SubprocessRunner


async def main() -> None:
    """Print timing, abort state and output of agy /usage runs."""
    for home in ("/data/home", "/tmp/fresh-home"):
        os.makedirs(home, exist_ok=True)
        print(f"=== HOME={home}")
        await run_once(home)


async def run_once(home: str) -> None:
    """Run agy /usage once with ``home`` as HOME."""
    runner = SubprocessRunner({"HOME": home}, cwd="/data/work")
    start = time.monotonic()
    try:
        result = await runner.run(
            ["agy", "-p", "/usage", "--output-format", "json"],
            time_limit=150,
            abort_on=("Authentication required",),
        )
    except TimeoutError as err:
        print(f"TIMEOUT after {time.monotonic() - start:.1f}s")
        print("STDOUT:", getattr(err, "stdout", "")[-1500:])
        print("STDERR:", getattr(err, "stderr", "")[-3000:])
        return
    print(
        f"rc={result.returncode} aborted={result.aborted} "
        f"after {time.monotonic() - start:.1f}s"
    )
    print("STDOUT:", result.stdout[-1500:])
    print("STDERR:", result.stderr[-3000:])


asyncio.run(main())
