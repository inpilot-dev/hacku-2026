"""Bounded Z3 model of two agents sharing one budget (contracts/README.md section 8).

Lives under services/api (not top-level verification/ as the build plan sketches)
so the API can import it.
"""

from .model import run
from .routes import build_router

__all__ = ["build_router", "run"]
