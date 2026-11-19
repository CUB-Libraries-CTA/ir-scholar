"""Compare bagit_builder.py's run time across --jobs and --processes settings.

Not part of the test suite; run it by hand:

    python test/benchmark.py                       # the zips in etds/
    python test/benchmark.py --source DIR          # the zips in DIR
    python test/benchmark.py --generate 12 40      # 12 generated zips with 40 MB PDFs
    python test/benchmark.py --config "--jobs 1" --config "--jobs 8" --repeats 5

Each configuration runs --repeats times on a fresh copy of the zips, interleaved
(A B A B ...) so caching and thermal drift affect them all alike. Only the bagit_builder
run itself is timed. Every run must sort the zips the same way, or the benchmark stops,
so a configuration can't win by doing something different.
"""

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from helpers import ETDS_DIR, copy_zips, make_big_zips, run_builder, summary

DEFAULT_CONFIGS = [
    "--jobs 1 --processes 1",
    "--jobs 1 --processes 4",
    "--jobs 4 --processes 1",
    "--jobs 4 --processes 4",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", type=Path, help=f"directory of zips (default: {ETDS_DIR})")
    source.add_argument(
        "--generate", nargs=2, type=int, metavar=("COUNT", "SIZE_MB"),
        help="benchmark COUNT generated zips, each with a SIZE_MB PDF, instead",
    )
    parser.add_argument("--repeats", type=int, default=3, help="runs per configuration (default: 3)")
    parser.add_argument(
        "--config", action="append", dest="configs", metavar="OPTIONS",
        help="bagit_builder options to compare, e.g. \"--jobs 4\" (repeatable; default: "
        "--jobs 1/4 x --processes 1/4)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configs = args.configs or DEFAULT_CONFIGS
    with tempfile.TemporaryDirectory(prefix="bagit_benchmark_") as work:
        work = Path(work)
        if args.generate:
            source = work / "source"
            make_big_zips(source, *args.generate)
        else:
            source = args.source or ETDS_DIR
        zips = sorted(source.glob("*.zip"))
        if not zips:
            sys.exit(f"No zips in {source}. Use --source DIR or --generate COUNT SIZE_MB.")
        size_mb = sum(path.stat().st_size for path in zips) / 1e6
        print(f"{len(zips)} zips ({size_mb:.0f} MB) from {source}, {args.repeats} runs each\n")

        times: dict[str, list[float]] = {config: [] for config in configs}
        expected = None
        for repeat in range(1, args.repeats + 1):
            for config in configs:
                run_dir = work / "run"
                shutil.rmtree(run_dir, ignore_errors=True)
                copy_zips(source, run_dir)
                start = time.perf_counter()
                result = run_builder(run_dir, *config.split())
                elapsed = time.perf_counter() - start
                outcome = sorted(
                    f"{folder.parent.name}/{folder.name}"
                    for folder in run_dir.glob("*/*") if folder.is_dir()
                )
                if expected is None:
                    expected = outcome
                    counts = summary(result.stdout)
                    print("every run must give: " + ", ".join(f"{n} {k}" for k, n in counts.items()) + "\n")
                elif outcome != expected:
                    sys.exit(f"'{config}' (run {repeat}) sorted the zips differently:\n{outcome}")
                times[config].append(elapsed)
                print(f"  run {repeat}  {config:<28} {elapsed:6.2f}s", flush=True)

    baseline = statistics.median(times[configs[0]])
    print(f"\n{'configuration':<28} {'median':>8} {'min':>8} {'max':>8}   vs {configs[0]}")
    for config in configs:
        median = statistics.median(times[config])
        ratio = baseline / median
        verdict = f"{ratio:.2f}x faster" if ratio > 1.005 else f"{1 / ratio:.2f}x slower" if ratio < 0.995 else "same"
        print(f"{config:<28} {median:7.2f}s {min(times[config]):7.2f}s {max(times[config]):7.2f}s   {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
