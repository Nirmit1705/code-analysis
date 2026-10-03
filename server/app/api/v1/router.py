"""Aggregates all /api/v1 routers. Later phases add their router here."""

from fastapi import APIRouter

from app.api.v1 import (
    analysis,
    api_reference,
    architecture,
    assistant,
    auth,
    codebase,
    compare,
    dashboard,
    debt,
    dependencies,
    github,
    impact,
    onboarding,
    tracer,
    webhooks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(github.router)
api_router.include_router(github.repos_router)
api_router.include_router(analysis.router)
api_router.include_router(dashboard.router)
api_router.include_router(architecture.router)
api_router.include_router(codebase.router)
api_router.include_router(dependencies.router)
api_router.include_router(debt.router)
api_router.include_router(api_reference.router)
api_router.include_router(tracer.router)
api_router.include_router(onboarding.router)
api_router.include_router(assistant.router)
api_router.include_router(compare.router)
api_router.include_router(impact.router)
api_router.include_router(webhooks.router)
