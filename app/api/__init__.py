"""
FastAPI REST & Server-Sent Events (SSE) API subsystem.
"""

# Starlette / FastAPI compatibility bridge:
try:
    import starlette.routing
    _orig_router_init = starlette.routing.Router.__init__

    def _safe_router_init(self, *args, **kwargs):
        kwargs.pop("on_startup", None)
        kwargs.pop("on_shutdown", None)
        return _orig_router_init(self, *args, **kwargs)

    starlette.routing.Router.__init__ = _safe_router_init
except (ImportError, AttributeError):
    pass
