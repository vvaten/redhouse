#!/usr/bin/env python3
"""Wrapper script for 5-minute energy meter aggregation."""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.aggregation.emeters_5min import main

if __name__ == "__main__":
    sys.exit(main())
