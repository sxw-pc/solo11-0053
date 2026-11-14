"""Stable, user-facing error types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GateSamplerError(Exception):
    """An expected failure with a stable code and an optional location."""

    code: str
    message: str
    location: str | None = None

    def __str__(self) -> str:
        where = f" @ {self.location}" if self.location else ""
        return f"{self.code}{where}: {self.message}"

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "location": self.location}
