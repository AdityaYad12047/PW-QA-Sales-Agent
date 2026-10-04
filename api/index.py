"""
api/index.py
Vercel Python Serverless Function entry point.
Adds backend directory to sys.path and exposes the FastAPI app.
"""
import sys
from pathlib import Path

# Add backend directory to sys.path so that `from app.xxx import ...` works in Vercel
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.main import app  # noqa: E402

# Vercel serverless function entrypoint
handler = app
