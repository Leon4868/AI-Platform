from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.core.tables import AuditEventRecord
from app.persistence.audit_service import audit_event_from_record, list_audit_events


def test_audit_record_mapping_preserves_append_only_event_shape() -> None:
    record = AuditEventRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        actor_id=uuid4(),
        action="workflow.published",
        resource_type="workflow_definition",
        resource_id=uuid4(),
        occurred_at=datetime.now(UTC),
        request_id="request-1",
        event_metadata={"revision": 2},
    )

    event = audit_event_from_record(record)

    assert event.id == record.id
    assert event.metadata == {"revision": 2}
    assert event.action == "workflow.published"


def test_audit_listing_is_tenant_scoped_and_bounded() -> None:
    statement = list_audit_events(uuid4(), limit=50, offset=10)
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()

    assert "tenant_id" in sql
    assert "order by" in sql
    assert " limit " in sql
    assert " offset " in sql
