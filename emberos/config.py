"""Minimal config stub -- collaborator's real config.py wasn't shared.

Cuma butuh ROOT_DIR buat tools.py/tasks.py/notes.py/app_launcher.py.
Kalau punya config.py asli dari collaborator, ganti file ini langsung.
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
