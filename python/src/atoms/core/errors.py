"""Engine error hierarchy (design §11)."""


class AtomsError(Exception):
    """Base class for every error the engine raises."""


class ProtocolError(AtomsError):
    """An internal engine contract was violated."""


class SpecValidationError(AtomsError):
    """A TransactionSpec failed compilation validation (design §5.4)."""


class ProjectApprovalRefused(AtomsError):
    """The rooted project proof failed (design §5.4).

    Distinct from SpecValidationError because the two proof stages are deliberately
    non-substitutable: a caller must not be able to catch one type and treat a
    project/root refusal as a compilation refusal.
    """


class PreconditionRefused(AtomsError):
    """Concurrent drift was detected; the transaction refuses cleanly."""


class CapabilityUnavailable(AtomsError):
    """A required filesystem capability is not supplied by the active backend."""


class TransactionHalted(AtomsError):
    """State is unattributable; the engine preserves the record and evidence."""
