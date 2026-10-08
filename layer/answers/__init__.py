"""B1's Answer shape and the reads that produce it.

Separate from `layer/mcp/` on purpose. PRD D2's argument for taking phase 5 before phase 4
is that phase 4 "adds no capability (one `run(transport=...)` argument)", and that is only
true if the answers exist independently of the transport that serves them. Everything here
takes a session and returns a shape; nothing here knows what MCP is.
"""

from layer.answers.shapes import (
    CANNOT_DETERMINE,
    CONFIDENCES,
    HIGH,
    MEDIUM,
    Answer,
    ProposedChange,
    sentences,
)

__all__ = [
    "Answer", "ProposedChange", "sentences", "HIGH", "MEDIUM", "CANNOT_DETERMINE", "CONFIDENCES",
]
