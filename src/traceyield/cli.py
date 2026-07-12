"""Console entry point for the ``traceyield`` command.

Wraps the report pipeline (``traceyield.report.main``) that used to run as
``python report.py``.
"""

import argparse
import sys

from traceyield import __version__
from traceyield import report as report_module


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # report.py's original `if __name__ == "__main__":` block special-cased
    # --machine-dir *before* running the pipeline, so run.cmd could resolve
    # this machine's artifact directory without paying for a full parse.
    # Preserve that exact semantics here.
    if "--machine-dir" in argv:
        print(report_module.MACHINE_DIR)
        return 0

    parser = argparse.ArgumentParser(
        prog="traceyield",
        description="Usage/health dashboard for Claude Code and Codex transcripts.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["report"],
        default=None,
        help="'report' parses transcripts, merges data, and regenerates "
             "report.html (equivalent to the old `python report.py`); "
             "omitted, this just prints usage and exits",
    )
    args = parser.parse_args(argv)

    # cli.main([]) (bare, no subcommand) is exercised directly by
    # test_packaging.py's test_main_no_args_returns_zero, which must return
    # fast and must never touch the user's real ~/.claude transcripts. The
    # real pipeline is therefore opt-in via the explicit `report` subcommand
    # rather than running unconditionally on a bare invocation, so the
    # console script stays cheap/side-effect-free until asked to do the
    # expensive thing. `traceyield report` (or `python -m traceyield report`)
    # is how the CLI actually generates the report.
    if args.command == "report":
        report_module.main()
        return 0

    print("traceyield: run `traceyield report` (or `python -m traceyield report`) "
          "to generate report.html (see README).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
