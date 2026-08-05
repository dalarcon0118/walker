"""
test_protocol_session_store.py — Unit tests del InMemorySessionRepository (F8).

Cubre: save/load/close con return pattern, y el lifecycle completo.
"""

import pytest


def test_session_store_save_load():
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    session, _ = McpSession.create()

    saved, errors = repo.save(session)
    assert saved is not None and not errors

    loaded, load_errors = repo.load(session.session_id.value)
    assert loaded is not None and not load_errors
    assert loaded.session_id.value == session.session_id.value


def test_session_store_load_missing():
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    session, errors = repo.load("no-existe")
    assert session is None and errors
    assert "not found" in errors[0]


def test_session_store_close():
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    session, _ = McpSession.create()
    repo.save(session)

    closed, errors = repo.close(session.session_id.value)
    assert closed and not errors
    assert repo.count() == 0


def test_session_store_close_missing():
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    closed, errors = repo.close("no-existe")
    assert not closed and errors


def test_session_store_lifecycle_full():
    """initialize → save → load → activate → close → confirmar cerrada."""
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.domain.values import SessionState
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    session, _ = McpSession.create()
    errs = session.initialize("test-client")
    assert not errs
    repo.save(session)

    loaded, _ = repo.load(session.session_id.value)
    assert loaded.state == SessionState.NEGOTIATED

    loaded.activate()
    assert loaded.state == SessionState.ACTIVE

    repo.save(loaded)
    closed, _ = repo.close(session.session_id.value)
    assert closed
    assert repo.count() == 0


def test_session_store_multiple_sessions():
    from features.cli_interface.domain.entities import McpSession
    from features.cli_interface.infrastructure.session_store import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    s1, _ = McpSession.create()
    s2, _ = McpSession.create()
    repo.save(s1)
    repo.save(s2)
    assert repo.count() == 2
    assert len(repo.list_sessions()) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
