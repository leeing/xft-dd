"""Compatibility wrapper for `xft web enrich`."""

import sys

from xft.cli.web import main

if __name__ == "__main__":
    raise SystemExit(main(["enrich", *sys.argv[1:]]))
