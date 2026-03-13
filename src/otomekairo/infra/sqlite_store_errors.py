"""Shared error types for SQLite store modules."""

from __future__ import annotations


# Block: Conflict error
class StoreConflictError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "conflict") -> None:
        # Block: Structured conflict payload
        super().__init__(message)
        self.message = message
        self.error_code = error_code


# Block: Validation error
class StoreValidationError(ValueError):
    pass
