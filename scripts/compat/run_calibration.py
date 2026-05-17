"""Compatibility wrapper for `xft calibrate`."""

from xft.cli.calibrate import main

if __name__ == "__main__":
    raise SystemExit(main())
