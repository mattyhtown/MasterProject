"""
Base agent class for all APEX-SHARPE agents.

Agents are stateless decision-makers. They receive context,
produce results, and do not hold mutable state between runs.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List

from ..types import AgentResult


class BaseAgent(ABC):
    """Base class for all APEX-SHARPE agents."""

    def __init__(self, name: str, config: Any = None):
        self.name = name
        self.config = config

    @abstractmethod
    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Execute the agent's primary task.

        Args:
            context: Dict containing all data the agent needs.

        Returns:
            AgentResult with the agent's output.
        """
        ...

    def _result(self, success: bool = True, data: Dict = None,
                messages: List = None, errors: List = None) -> AgentResult:
        """Convenience helper to build an AgentResult."""
        return AgentResult(
            agent_name=self.name,
            timestamp=datetime.now(),
            success=success,
            data=data or {},
            messages=messages or [],
            errors=errors or [],
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
