"""Tahmin servisi giriş noktası.

Çalıştırma:  uvicorn api:app --port 8000     (belgeler: http://localhost:8000/docs, kılavuz: docs/api.md)
"""

from sogutma.api import app  # noqa: F401
