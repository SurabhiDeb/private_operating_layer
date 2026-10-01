"""Measurement: reading a number a source names, computing one it does not, or saying
it cannot be computed here. See `engine.py` for why the third case is a feature."""

from layer.metrics.engine import (
    BAD_DEF,
    MISSING_DATA,
    NO_METRIC,
    SUPPORTED_KINDS,
    MetricValue,
    Result,
    Unmeasurable,
    compute,
    supports,
)
from layer.metrics.predicates import BadDefinition, evaluate, to_bool, truthy

__all__ = [
    "compute", "supports", "MetricValue", "Unmeasurable", "Result",
    "SUPPORTED_KINDS", "NO_METRIC", "MISSING_DATA", "BAD_DEF",
    "evaluate", "to_bool", "truthy", "BadDefinition",
]
