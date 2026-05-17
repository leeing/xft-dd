"""Compatibility wrapper for `xft web import`."""

import sys

from xft.cli.web import main

if __name__ == "__main__":
    raise SystemExit(main(["import", *sys.argv[1:]]))
