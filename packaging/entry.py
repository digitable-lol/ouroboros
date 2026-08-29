"""Frozen-binary entry point: import the package by absolute name so the CLI's
relative imports resolve (PyInstaller runs the entry script as __main__)."""

import sys

from ouroboros.cli import main

if __name__ == "__main__":
    sys.exit(main())
