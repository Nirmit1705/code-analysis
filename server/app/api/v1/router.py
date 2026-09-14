"""Aggregates all /api/v1 routers. Later phases add their router here."""

from fastapi import APIRouter

from app.api.v1 import analysis, architecture, auth, codebase, dashboard, dependencies, github

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(github.router)
api_router.include_router(github.repos_router)
api_router.include_router(analysis.router)
api_router.include_router(dashboard.router)
api_router.include_router(architecture.router)
api_router.include_router(codebase.router)
api_router.include_router(dependencies.router)
