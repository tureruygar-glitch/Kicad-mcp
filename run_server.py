"""Convenience launcher: ``python run_server.py`` starts the KiCad 10 MCP server.

Equivalent to ``python -m kicad10_mcp``. Useful for MCP client configs that
point at an absolute script path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kicad10_mcp.server import main

if __name__ == "__main__":
    main()
