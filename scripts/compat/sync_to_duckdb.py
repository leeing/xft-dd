"""Compatibility wrapper for `xft cache sync-remote`."""

import sys

from xft.cli.cache import main

if __name__ == "__main__":
    raise SystemExit(main(["sync-remote", *sys.argv[1:]]))
