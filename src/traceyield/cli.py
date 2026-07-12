"""Console entry point for the ``traceyield`` command.

This is a minimal dispatcher for now: real subcommands (report
generation, canonical store maintenance, etc.) land in a later story
once report.py/canonical.py are moved under src/traceyield. For S1 it
only needs to parse arguments, support --help/--version, and exit 0.
"""

import argparse
import sys

from traceyield import __version__


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="traceyield",
        description="Usage/health dashboard for Claude Code and Codex transcripts.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.parse_args(argv)

    print("traceyield: run `python report.py` to generate report.html (see README).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
