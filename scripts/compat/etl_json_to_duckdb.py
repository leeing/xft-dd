"""Compatibility wrapper for `xft warehouse build`."""

import sys

from xft.cli.warehouse import main

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))
