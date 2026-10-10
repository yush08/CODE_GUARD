"""Versioned API router aggregation (/api/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import analysis, faculty, health, problems, submissions, system

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(problems.router)
api_router.include_router(submissions.router)
api_router.include_router(analysis.router)
api_router.include_router(faculty.router)
