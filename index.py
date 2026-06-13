"""Vercel serverless entrypoint. Exposes the Flask app as the WSGI handler."""
import os
import sys

# make the project root importable so `import app` / `import providers` work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel's Python runtime serves this `app`)
