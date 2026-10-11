"""ENG-001: local, structural event-record validation only."""

from .record_validation import CONTRACT_VERSION, validate_event

__all__ = ["CONTRACT_VERSION", "validate_event"]
