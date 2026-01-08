#!/usr/bin/env python3
"""Wrapper script for 15-minute analytics aggregation."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.aggregation.analytics_15min import main

if __name__ == "__main__":
    sys.exit(main())
