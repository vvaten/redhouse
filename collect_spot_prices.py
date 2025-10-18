#!/usr/bin/env python3
"""Wrapper script for spot price collection to maintain backward compatibility."""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.data_collection.spot_prices import main

if __name__ == "__main__":
    sys.exit(main())
