"""Response contracts for the health surfaces.

PROJECT_BLUEPRINT.md §28.10 keeps liveness, readiness, and dependency detail as
distinct surfaces. Liveness and readiness are unauthenticated, so they answer only
the question an orchestrator asks and disclose nothing about the components
behind them. The detailed dependency surface arrives with authentication, where
it can be restricted to administrators.
"""

from typing import Literal

from pydantic import BaseModel, Field


class LivenessResponse(BaseModel):
    """The process is running and able to serve requests."""

    status: Literal["alive"] = Field(description="Always 'alive' when the process responds.")


class ReadinessResponse(BaseModel):
    """Whether every essential dependency is currently healthy."""

    ready: bool = Field(description="True when no essential dependency is unhealthy.")
