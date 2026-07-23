import pytest

from atoms.core.errors import (
    AtomsError,
    CapabilityUnavailable,
    PreconditionRefused,
    ProtocolError,
    SpecValidationError,
    TransactionHalted,
)


def test_all_engine_errors_subclass_atoms_error():
    for exc in (
        ProtocolError,
        SpecValidationError,
        PreconditionRefused,
        CapabilityUnavailable,
        TransactionHalted,
    ):
        assert issubclass(exc, AtomsError)


def test_errors_carry_a_message():
    with pytest.raises(CapabilityUnavailable, match="atomic_exchange"):
        raise CapabilityUnavailable("atomic_exchange missing on volume")
