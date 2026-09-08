"""Ponto de entrada para `python -m nsrecruiter`."""
from __future__ import annotations

import sys

from nsrecruiter.cli import main

if __name__ == "__main__":
    sys.exit(main())
