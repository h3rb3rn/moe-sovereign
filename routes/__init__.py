"""routes — FastAPI APIRouter modules extracted from main.py."""

from .health            import router as health_router
from .watchdog          import router as watchdog_router
from .mission_context   import router as mission_context_router
from .graph             import router as graph_router
from .admin_benchmark   import router as admin_benchmark_router
from .admin_ontology    import router as admin_ontology_router
from .admin_stats       import router as admin_stats_router
from .feedback          import router as feedback_router
from .ollama_compat     import router as ollama_compat_router

__all__ = [
    "health_router",
    "watchdog_router",
    "mission_context_router",
    "graph_router",
    "admin_benchmark_router",
    "admin_ontology_router",
    "admin_stats_router",
    "feedback_router",
    "ollama_compat_router",
]
