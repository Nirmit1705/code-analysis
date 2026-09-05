"""Celery tasks for the analysis pipeline.

Empty for now — Phase 5.3 adds `run_repository_analysis(analysis_run_id)`
here (orchestrating the QUEUED -> CLONING -> ... -> COMPLETED/FAILED state
machine over Phase 5.1's `create_queued_analysis_run` / `persist_analysis_run`
/ `mark_analysis_run_failed`). This module exists now, ahead of that, purely
so `workers/celery_app.py`'s `include=[...]` has a real module to import at
worker startup — task discovery wired up early, no tasks defined yet.
"""
