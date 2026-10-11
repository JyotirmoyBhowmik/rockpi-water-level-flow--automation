"""
Radxa ROCK Pi S IoT Automation & Precision Irrigation Controller.
Production runtime for RK3308 Armbian/Debian headless.
"""

__version__ = "1.0.0"

# Starlette / FastAPI compatibility bridge:
# In newer Starlette versions (0.42+), on_startup and on_shutdown were removed from Router.__init__
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
