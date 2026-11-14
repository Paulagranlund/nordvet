"""
Run the whole pipeline: load -> transform (+ checks) -> dashboard.

Usage:
    python -m pipeline.run           # incremental
    python -m pipeline.run --full    # full reload first

This is the one command a scheduler (e.g. Azure) would run every morning.
"""

import argparse
import logging

from pipeline import dashboard, load, transform

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="reload all appointments")
    args = parser.parse_args()

    load.run(full=args.full)   # each step exits with an error if it fails,
    transform.run()            # so later steps never run on bad data
    dashboard.run()
