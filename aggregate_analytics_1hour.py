#!/usr/bin/env python3
"""Wrapper script for 1-hour analytics aggregation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.aggregation.run_analytics_1hour import main

if __name__ == "__main__":
    sys.exit(main())
