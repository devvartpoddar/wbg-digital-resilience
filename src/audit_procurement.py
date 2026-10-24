#!/usr/bin/env python3
"""The procurement audit (src/audit.py --procurement), kept under this name
while ./wbg calls it."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402

if __name__ == "__main__":
    sys.exit(audit.main(["--procurement", *sys.argv[1:]]))
