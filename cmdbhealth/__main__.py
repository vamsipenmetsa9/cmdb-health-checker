from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from .checks import DATE_FORMAT, load_csv, run_checks

log = logging.getLogger("cmdbhealth")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cmdbhealth", description="Score a CMDB export for completeness and correctness.")
    parser.add_argument("--cis", required=True)
    parser.add_argument("--relationships", required=True)
    parser.add_argument("--today", default=datetime.now().strftime(DATE_FORMAT), help="YYYY-MM-DD, for repeatable runs")
    parser.add_argument("--stale-days", type=int, default=60)
    parser.add_argument("--min-score", type=float, default=0.0, help="exit 1 if either score is below this")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        report = run_checks(load_csv(args.cis), load_csv(args.relationships), datetime.strptime(args.today, DATE_FORMAT), args.stale_days)
    except (OSError, ValueError) as exc:
        log.error("could not run checks: %s", exc)
        return 2
    print(f"CIs checked      : {report.total}")
    print(f"Completeness     : {report.completeness}%  ({len(report.incomplete)} incomplete)")
    print(f"Correctness      : {report.correctness}%  ({len(report.duplicates)} duplicate groups, {len(report.stale)} stale, {len(report.orphans)} orphans)")
    for row in report.invalid_rows:
        log.warning("skipped row: %s", row)
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2)
    return 1 if min(report.completeness, report.correctness) < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
