"""Domain-level exceptions. Adapters translate these to transport errors."""


class IFAPError(Exception):
    """Base class for all platform errors."""


class DomainRuleViolationError(IFAPError):
    """A business invariant was violated."""


class NotFoundError(IFAPError):
    """A requested entity does not exist."""


class AgentExecutionError(IFAPError):
    """An agent failed after exhausting its retry budget."""


class AgentNotRegisteredError(IFAPError):
    """The workflow references an agent that no plugin registered."""
