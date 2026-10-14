import errno
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from stepwise.jobs import is_retryable


@pytest.mark.parametrize(
    "exc,expected",
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (OSError(errno.EBUSY, "temporary"), True),
        (OSError(errno.ENOSPC, "full"), False),
        (PermissionError(), False),
        (ValueError(), False),
        (FileNotFoundError(), False),
    ],
)
def test_retry_classification_is_explicit(exc, expected):
    assert is_retryable(exc) is expected


def test_operational_error_without_connection_evidence_is_not_retried():
    error = OperationalError("SELECT * FROM nonexistent", {}, sqlite3.OperationalError("no such table"))
    assert not is_retryable(error)
    disconnected = OperationalError("SELECT 1", {}, ConnectionError(), connection_invalidated=True)
    assert is_retryable(disconnected)
