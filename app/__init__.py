"""
Radxa ROCK Pi S IoT Automation & Precision Irrigation Controller.
Production runtime for RK3308 Armbian/Debian headless.
"""

__version__ = "1.0.0"

# Starlette / FastAPI compatibility bridge:
# In newer Starlette versions (0.42+), on_startup and on_shutdown were removed from Router.__init__,
# and Starlette.build_middleware_stack() checks self.max_body_size.
try:
    import starlette.routing
    _orig_router_init = starlette.routing.Router.__init__

    def _safe_router_init(self, *args, **kwargs):
        kwargs.pop("on_startup", None)
        kwargs.pop("on_shutdown", None)
        res = _orig_router_init(self, *args, **kwargs)
        if not hasattr(self, "on_startup"):
            self.on_startup = []
        if not hasattr(self, "on_shutdown"):
            self.on_shutdown = []
        return res

    starlette.routing.Router.__init__ = _safe_router_init
except (ImportError, AttributeError):
    pass

try:
    from fastapi import FastAPI
    if not hasattr(FastAPI, "max_body_size"):
        FastAPI.max_body_size = None
except (ImportError, AttributeError):
    pass
