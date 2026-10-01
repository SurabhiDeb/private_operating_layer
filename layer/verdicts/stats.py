"""The Wilson score interval.

Reimplemented rather than imported: the reference fixtures carry this in their own
`shared/stats.py`, and a fixture may never be a dependency of the Layer (PRD Appendix F).
The constant is theirs, deliberately, so that a number the Layer reports and a number the
product reports agree to the last digit — which is how the prototype's
`[0.6456695649333126, 1.0]` for seven of seven was identified as this rule rather than
some other.

**Why an interval at all.** A bare pass rate is close to meaningless. 19/20 and 190/200 are
both "95%" and they are very different claims, and an eval suite on its first week lives
entirely in the first case. The textbook normal interval returns zero width at 0/20 and
20/20, claiming certainty from twenty samples; Wilson stays sensible at the edges and at
small n, which is where every clause starts.
"""

from __future__ import annotations

import math

#: z for a two-sided 95% interval. The same constant the fixtures use.
Z_95 = 1.959963984540054


def wilson(passed: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Lower and upper bounds for a proportion."""
    if total <= 0:
        raise ValueError(f"total must be positive, got {total}")
    if not 0 <= passed <= total:
        raise ValueError(f"passed must be between 0 and {total}, got {passed}")

    n = float(total)
    p = passed / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    spread = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - spread), min(1.0, centre + spread)
