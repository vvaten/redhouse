#!/usr/bin/env python3
"""Wrapper script for CheckWatt collection to maintain backward compatibility."""

import os
import sys

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.data_collection.checkwatt import main

if __name__ == "__main__":
    sys.exit(main())
