"""Lab compatibility launcher: runs from a checkout without installing the package."""
import sys
from pathlib import Path

# Prefer this checkout's source over any installed copy, so `python3 server.py` (the
# command .mcp.json and client.py use) never depends on how the package was installed.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fastflights_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
