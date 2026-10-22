"""WSGI entry point for PythonAnywhere."""

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
project_path = str(PROJECT_DIR)
if project_path not in sys.path:
    sys.path.insert(0, project_path)

from app import app as application
