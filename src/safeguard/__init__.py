"""SafeGuard — a real-time content safety decisioning platform.

SafeGuard applies the architecture of production financial fraud detection to
content safety: a layered decision pipeline where cheap deterministic rules run
first, probabilistic models run second, and an explicit policy layer resolves
their signals into a single auditable verdict inside a fixed latency budget.

Public surface::

    from safeguard import Decision, ModerationRequest, Verdict, build_pipeline
"""

from safeguard.core.models import (
    Category,
    Decision,
    ModerationRequest,
    Signal,
    SignalSource,
    Verdict,
)
from safeguard.core.pipeline import Pipeline, build_pipeline

__version__ = "0.1.0"

__all__ = [
    "Category",
    "Decision",
    "ModerationRequest",
    "Pipeline",
    "Signal",
    "SignalSource",
    "Verdict",
    "__version__",
    "build_pipeline",
]
