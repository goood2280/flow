"""app_v2.orchestrator — agent-task orchestration scaffold.

This package keeps agentic work explicit and inspectable:
- task contracts are JSON-shaped and versioned
- domain services remain deterministic and testable
- internal APIs can be connected later without changing business semantics
"""

from .schemas import (
    ActionProposal,
    AgentTaskRequest,
    AgentTaskResult,
    OrchestrationPlan,
    OrchestrationRun,
)
from .service import OrchestratorService

__all__ = [
    "ActionProposal",
    "AgentTaskRequest",
    "AgentTaskResult",
    "OrchestrationPlan",
    "OrchestrationRun",
    "OrchestratorService",
]
