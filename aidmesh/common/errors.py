"""Typed failures used by protocol state machines and invariant checks."""


class ProtocolError(Exception):
    """Base class for an expected protocol-level failure."""


class ConfigurationError(ProtocolError, ValueError):
    """A campaign or simulator configuration is internally inconsistent."""


class ValidationError(ProtocolError, ValueError):
    """An input is not valid for the current protocol state."""


class DuplicateError(ValidationError):
    """An at-most-once identifier was used more than once."""


class InvariantViolation(ProtocolError, AssertionError):
    """A safety invariant does not hold for an observed state or trace."""
