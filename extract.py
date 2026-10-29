#!/usr/bin/env python3
"""
PheBee Local Extraction Orchestrator - command-line entry point.

The implementation lives in the ``phebee_extraction`` package. This script is kept so that
``python extract.py ...`` keeps working, and so ``import extract`` still exposes the public
API for existing callers.
"""

import sys

from phebee_extraction import *  # noqa: F401,F403 - compatibility re-export
from phebee_extraction import __version__  # noqa: F401
from phebee_extraction.cli import main, parse_arguments  # noqa: F401

if __name__ == "__main__":
    sys.exit(main())
