#!/usr/bin/env python3
"""Wrapper script for the heating program fallback copy."""

import os
import sys

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.control.program_fallback import main

if __name__ == "__main__":
    sys.exit(main())
