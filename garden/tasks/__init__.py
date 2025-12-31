"""Task registry for garden."""

from .base import (  # noqa: F401
    Artifact,
    REGISTRY,
    Task,
    TaskContext,
    available,
    get,
    load_all,
    register,
)

load_all()
