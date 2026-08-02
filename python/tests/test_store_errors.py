"""Design §9.1 -- narrow, result-code translation with a bare default re-raise."""

from __future__ import annotations

import sqlite3

import pytest

from atoms.core.errors import AtomsError
from atoms.store.errors import (
    SQLITE_CORRUPT,
    SQLITE_NOTADB,
    MetadataStoreInvalid,
    translated,
)


def test_metadata_store_invalid_is_an_atoms_error():
    assert issubclass(MetadataStoreInvalid, AtomsError)


def test_a_garbage_file_translates_to_metadata_store_invalid(tmp_path):
    """Reading a non-database raises exactly sqlite3.DatabaseError with SQLITE_NOTADB --
    not OperationalError, so no narrower except clause can reach it."""
    path = tmp_path / "garbage.db"
    path.write_bytes(b"this is not a database" * 64)
    connection = sqlite3.connect(path)
    with pytest.raises(MetadataStoreInvalid) as caught, translated("reading the schema"):
        connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert "reading the schema" in str(caught.value)


def test_an_operational_error_propagates_unchanged(tmp_path):
    connection = sqlite3.connect(tmp_path / "ok.db")
    with pytest.raises(sqlite3.OperationalError) as caught, translated("selecting"):
        connection.execute("SELECT * FROM nope")
    assert not isinstance(caught.value, MetadataStoreInvalid)


def test_a_programming_error_carrying_no_result_code_propagates(tmp_path):
    """sqlite3.ProgrammingError is a DatabaseError with no sqlite_errorcode attribute at
    all. Reading the attribute directly would raise AttributeError from inside the
    handler, replacing a clear message with a failure in the error path."""
    connection = sqlite3.connect(tmp_path / "closed.db")
    connection.close()
    with pytest.raises(sqlite3.ProgrammingError) as caught, translated("using a closed connection"):
        connection.execute("SELECT 1")
    assert not hasattr(caught.value, "sqlite_errorcode")


def test_an_extended_result_code_still_matches_its_primary(tmp_path):
    """Discrimination is on `code & 0xFF`, so SQLITE_CORRUPT_VTAB and friends match --
    the idiom A4a already uses for SQLITE_BUSY at probe.py:367."""
    extended = SQLITE_CORRUPT | (1 << 8)
    error = sqlite3.DatabaseError("synthetic")
    error.sqlite_errorcode = extended  # type: ignore[attr-defined]
    with pytest.raises(MetadataStoreInvalid), translated("synthetic"):
        raise error


def test_an_unknown_result_code_keeps_its_class_and_traceback():
    error = sqlite3.DatabaseError("unknown")
    error.sqlite_errorcode = 0x7F  # type: ignore[attr-defined]
    with pytest.raises(sqlite3.DatabaseError) as caught, translated("synthetic"):
        raise error
    assert caught.value is error


def test_the_translated_codes_are_the_two_the_design_names():
    assert (SQLITE_CORRUPT, SQLITE_NOTADB) == (11, 26)


def test_a_non_sqlite_exception_is_untouched():
    with pytest.raises(ValueError), translated("synthetic"):
        raise ValueError("not sqlite's problem")
