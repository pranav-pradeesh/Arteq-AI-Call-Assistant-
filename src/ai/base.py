"""Shared AI result types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrainResult:
    text: str
    language: str
    should_transfer: bool = False
    should_end: bool = False
    latency_ms: int = 0
