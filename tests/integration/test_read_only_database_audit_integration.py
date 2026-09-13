from __future__ import annotations

import pytest
from sqlalchemy import literal, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tests.support.read_only_database_audit import (
    AuditStage,
    ConditionRecorder,
    ConditionState,
    run_read_only_database_audit,
)

pytestmark = pytest.mark.asyncio

CONDITION_NAMES = ("CORE_SELECT_ONE",)


async def test_real_postgresql_read_only_audit_boundary(
    test_database_url: str,
    test_engine: AsyncEngine,
) -> None:
    del test_engine  # The fixture guarantees a migrated, identity-checked database.
    expected_database = make_url(test_database_url).database
    assert expected_database is not None

    async def query(connection: AsyncConnection, recorder: ConditionRecorder) -> None:
        selected = (await connection.execute(select(literal(1)))).scalar_one()
        recorder.record("CORE_SELECT_ONE", selected == 1)

    result = await run_read_only_database_audit(
        condition_names=CONDITION_NAMES,
        test_database_url=test_database_url,
        expected_database=expected_database,
        query=query,
    )

    assert result.stage_sequence == tuple(AuditStage)
    assert tuple(item.name for item in result.conditions) == CONDITION_NAMES
    assert result.state_for("CORE_SELECT_ONE") is ConditionState.TRUE
    assert result.first_failed_stage is None
    assert result.harness_error is None
    assert result.cleanup_failures == ()
    assert result.harness_valid is True
