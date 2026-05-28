# patent_analysis/install_dependencies.py
"""
Dependency bootstrap for the patent_analysis package.

Call _ensure_deps() once at import time of patent_processor.py (the entry
point).  All other modules in the package can then import freely because
the packages are guaranteed to be installed before they are needed.

The notebook no longer needs to import or call InstallDependencies directly.
"""

import os, sys

def _silent(cmd: str) -> None:
    """Run a shell command, suppressing all output."""
    os.system(f"{cmd} > /dev/null 2>&1")


def _ensure_deps() -> None:
    """
    Ensure every third-party package required by patent_analysis is installed.
    Called once from patent_processor.py before any other package import.
    Missing packages are installed silently via pip.
    """

    # epo-tipdata-ops
    try:
        from epo.tipdata.ops import OPSClient  # noqa: F401
    except ImportError:
        print("📦 Installing epo-tipdata-ops …")
        _silent("pip install epo-tipdata-ops")

    # python-epo-ops-client
    try:
        import epo_ops  # noqa: F401
    except ImportError:
        print("📦 Installing python-epo-ops-client …")
        _silent("pip install python-epo-ops-client")

    # ipywidgets
    try:
        import ipywidgets  # noqa: F401
    except ImportError:
        print("📦 Installing ipywidgets …")
        _silent("pip install ipywidgets")

    # plotly
    try:
        import plotly  # noqa: F401
    except ImportError:
        print("📦 Installing plotly …")
        _silent("pip install plotly")

    # Pillow (PIL) - used by tree_processor for image handling
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("📦 Installing Pillow …")
        _silent("pip install Pillow")

    # openpyxl
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("📦 Installing openpyxl …")
        _silent("pip install openpyxl")

    # pandas (should already be present in any Jupyter env)
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("📦 Installing pandas …")
        _silent("pip install pandas")
        
    # requests-cache — shared SQLite HTTP cache for OPS legal-event fetches.
    # Eliminates redundant network calls on repeated Submit clicks for the
    # same patent family (legal events are immutable once recorded).
    try:
        import requests_cache  # noqa: F401
    except ImportError:
        print("📦 Installing requests-cache …")
        _silent("pip install requests-cache")
        
    # Configure pandas display options
    try:
        import pandas as pd
        pd.set_option("display.max_columns", None)
        pd.set_option("display.expand_frame_repr", False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Legacy shim: keeps old code that calls InstallDependencies() working
# ---------------------------------------------------------------------------
class InstallDependencies:
    """Deprecated. Kept for backward compatibility only — use _ensure_deps()."""
    def __init__(self):
        _ensure_deps()
