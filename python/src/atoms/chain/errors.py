"""Chain evidence errors."""

from atoms.core.errors import AtomsError


class ChainStateInvalid(AtomsError):
    """The project-local chain cannot be interpreted safely."""
