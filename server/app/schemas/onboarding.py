"""`GET /repos/{id}/onboarding` and `POST .../onboarding/progress` schemas
(Phase 10.10).

Every number here is a count the analysis already recorded (files, lines,
endpoints, importers, complexity) — no time estimates, no difficulty levels
(decision #2). Reasons are machine-readable literals with the count that backs
them, so the screen phrases them and can never drift from the fact.

Deviation from the plan's response sketch: there is no free-text `cycle_note`.
Components that import each other are grouped by a structured
`in_cycle_with` on each step instead, so the screen can show the grouping
next to the steps themselves.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.architecture import ModuleKindLiteral

# Why a file is suggested inside a step. `count` is: endpoints the file
# declares / distinct files importing it / its average function complexity /
# its line count.
ReadFirstReason = Literal["endpoints", "imported_by", "complexity", "largest"]

# Why a file is a repository-wide starting point. `count` is: none for a
# README / the number of source files it reaches through imports / distinct
# files importing it.
StartingPointReason = Literal["readme", "entry_point", "imported_by"]


class ReadFirstFile(BaseModel):
    path: str
    reason: ReadFirstReason
    count: int


class StartingPoint(BaseModel):
    path: str
    reason: StartingPointReason
    count: int | None = None


class OnboardingStep(BaseModel):
    # The Architecture component id — also the progress key.
    id: str
    title: str
    kind: ModuleKindLiteral
    file_count: int
    loc: int
    endpoint_count: int
    # Up to a handful of "METHOD /path" strings, for a step that serves endpoints.
    sample_apis: list[str] = []
    # Distinct path components that import this one.
    depended_on_by: int
    # Ids of earlier steps this one imports directly (its prerequisites).
    builds_on: list[str] = []
    # Ids of other steps that import this one and are imported by it (a
    # component-level cycle): no foundations-first order exists between them,
    # so they sit next to each other and are meant to be read together.
    in_cycle_with: list[str] = []
    read_first: list[ReadFirstFile] = []
    done: bool = False
    completed_at: datetime | None = None


class OnboardingRead(BaseModel):
    """`analyzed=False` is the honest "not analyzed yet" payload, every other
    field at its empty default — same convention as `ArchitectureRead`."""

    analyzed: bool
    analysis_run_id: uuid.UUID | None = None
    steps: list[OnboardingStep] = []
    starting_points: list[StartingPoint] = []
    completed_count: int = 0
    # How many components qualified for the path before the cap, so the screen
    # can say "the N most connected of M" when some were left out.
    total_components: int = 0


class OnboardingProgressUpdate(BaseModel):
    step_id: str
    done: bool


class OnboardingProgressRead(BaseModel):
    step_id: str
    done: bool
    completed_at: datetime | None = None
    completed_count: int
    step_count: int
