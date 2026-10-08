"""Where proposals come from. Phase 5 step 16.

The module is `generators` and the function it exports is `generate`. Named apart on
purpose: a module and a re-exported function sharing one name means
`from layer.proposals import generate` silently hands back whichever was imported last.
"""

from layer.proposals.generators import GeneratorRun, generate

__all__ = ["GeneratorRun", "generate"]
