"""jevmind — one brain, many hands. Typed decisions, a gate in code, every answer on the record."""

from .mind import Mind
from .questions import Answer, Choice, Noul, Score

__version__ = "0.1.0"
__all__ = ["Mind", "Noul", "Choice", "Score", "Answer"]
