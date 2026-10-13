"""Shared project paths for local tools, storage, and launchers."""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def data_dir(root_dir=None) -> Path:
    """Return the writable data directory for this EmberOS installation."""
    configured = os.environ.get("EMBER_DATA_DIR")
    path = Path(configured).expanduser() if configured else Path(root_dir or ROOT_DIR) / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir(root_dir=None) -> Path:
    """Return the writable log directory for this EmberOS installation."""
    path = data_dir(root_dir) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
