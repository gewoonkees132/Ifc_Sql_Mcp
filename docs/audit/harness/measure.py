"""Run a command and report its wall time and the peak resident memory of its children.

Usage: python -I measure.py <command> [args...]

Prints ``MEASURE exit=… wall_s=… peak_rss_mb=…`` on stderr; used for the ingest and dq
timings in the audit (the container has no /usr/bin/time).
"""

import resource
import subprocess
import sys
import time


def main() -> int:
    """Run ``sys.argv[1:]`` and print the measurement line."""
    start = time.perf_counter()
    code = subprocess.call(sys.argv[1:])  # noqa: S603 (the command is the caller's own)
    wall = time.perf_counter() - start
    peak_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    sys.stderr.write(f"MEASURE exit={code} wall_s={wall:.1f} peak_rss_mb={peak_mb:.0f}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
