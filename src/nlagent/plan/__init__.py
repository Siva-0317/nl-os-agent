"""
src/nlagent/plan - Multi-Step Plan Engine with Copy-on-Write Virtual Filesystem Overlay.
"""

from nlagent.plan.engine import (
    MAX_PLAN_STEPS,
    CombinedPlanPreview,
    MultiStepPlan,
    PlanEngine,
    PlanError,
    PlanExecutionResult,
    PlanLimitExceededError,
    PlanRollbackError,
    PlanStep,
    StepExecutionRecord,
    UndoGroup,
    UndoGroupNotFoundError,
    UndoGroupResult,
)

__all__ = [
    "MAX_PLAN_STEPS",
    "PlanError",
    "PlanLimitExceededError",
    "PlanRollbackError",
    "UndoGroupNotFoundError",
    "PlanStep",
    "CombinedPlanPreview",
    "MultiStepPlan",
    "StepExecutionRecord",
    "UndoGroup",
    "PlanExecutionResult",
    "UndoGroupResult",
    "PlanEngine",
]

