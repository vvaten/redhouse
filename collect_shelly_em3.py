#!/usr/bin/env python3
"""Wrapper script for Shelly EM3 collection."""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.data_collection.shelly_em3 import main

if __name__ == "__main__":
    sys.exit(main())
