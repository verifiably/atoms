"""Chain evidence errors."""

from atoms.core.errors import AtomsError


class ChainStateInvalid(AtomsError):
    """The project-local chain cannot be interpreted safely."""


class PendingUnresolved(AtomsError):
    """The chain carries a registration that survived recovery unsettled.

    A direct `AtomsError` subclass rather than a `PreconditionRefused` or a
    `ChainStateInvalid` one, because the three refusals are non-substitutable: the
    chain is not invalid -- it is well-formed and says something definite -- and a
    consumer catching concurrent-drift refusals must not swallow "your evidence is
    gone, and no retry will bring it back."
    """
