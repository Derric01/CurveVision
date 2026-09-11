"""Aggregates every v1 router."""

from __future__ import annotations

from fastapi import APIRouter

# Aliased because `annotations` is also the name of the `__future__` import above; the
# collision is legal but reads as a bug.
from curvevision.api.v1 import annotations as annotations_api
from curvevision.api.v1 import (
    auth,
    datasets,
    local,
    models,
    organizations,
    projects,
    review,
    system,
    tasks,
    webhooks,
)

api_router = APIRouter()

for module_router in (
    system.router,
    auth.router,
    organizations.router,
    projects.router,
    tasks.router,
    local.router,
    annotations_api.router,
    review.router,
    datasets.router,
    models.router,
    models.inference_router,
    webhooks.router,
):
    api_router.include_router(module_router)

__all__ = ["api_router"]
