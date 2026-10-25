#!/usr/bin/env python3
"""The procurement audit (src/wbg_portfolio/audit.py --procurement), kept under this name
while ./wbg_portfolio calls it."""
import os, sys

from wbg_portfolio import audit  # noqa: E402

if __name__ == "__main__":
    sys.exit(audit.main(["--procurement", *sys.argv[1:]]))
