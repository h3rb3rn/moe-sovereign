"""routes — FastAPI APIRouter modules extracted from main.py."""

from .health         import router as health_router
from .watchdog       import router as watchdog_router
from .mission_context import router as mission_context_router
from .graph          import router as graph_router

__all__ = ["health_router", "watchdog_router", "mission_context_router", "graph_router"]
