"""Compatibility wrapper for `xft runs inspect`."""

import sys

from xft.cli.runs import main

if __name__ == "__main__":
    raise SystemExit(main(["inspect", *sys.argv[1:]]))
