"""Domain model for model-spend governance.

Amounts are `Decimal` throughout. Token pricing produces fractions of a cent, and
binary floats would drift a running total that a finance system later reconciles.

Departments are opaque strings, matching how the platform already carries them on
`PermissionSnapshot.department_ids`.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.core.schemas import ApiModel, Entity

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"

DepartmentId = str


def billing_period(moment: datetime) -> str:
    """The monthly bucket a moment falls into, as ``YYYY-MM`` in UTC.

    Derived from the usage timestamp rather than accepted from the caller, so a
    late-arriving record lands in the month it was actually incurred.

    Converting to UTC first is what makes the boundary unambiguous: the same
    instant reported from Shanghai and from Los Angeles has to land in one
    month, not in whichever one the reporter's calendar happened to show.
    """
    return f"{moment.astimezone(UTC):%Y-%m}"


class BudgetScope(StrEnum):
    TENANT = "tenant"
    DEPARTMENT = "department"


class Budget(Entity):
    scope: BudgetScope
    department_id: DepartmentId | None = Field(default=None, min_length=1, max_length=100)
    period: str = Field(pattern=MONTH_PATTERN)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    soft_limit: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    hard_limit: Decimal = Field(gt=0, max_digits=18, decimal_places=6)

    @model_validator(mode="after")
    def _coherent(self) -> "Budget":
        if self.scope is BudgetScope.DEPARTMENT and self.department_id is None:
            raise ValueError("a department budget must name a department")
        if self.scope is BudgetScope.TENANT and self.department_id is not None:
            raise ValueError("a tenant budget must not name a department")
        if self.soft_limit > self.hard_limit:
            raise ValueError("soft_limit must not exceed hard_limit")
        return self


class BudgetRequest(ApiModel):
    scope: BudgetScope
    department_id: DepartmentId | None = Field(default=None, min_length=1, max_length=100)
    period: str = Field(pattern=MONTH_PATTERN)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    soft_limit: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    hard_limit: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class UsageRequest(ApiModel):
    """One billable model call, as reported by whoever made it.

    Every field here is part of the idempotency fingerprint: reusing a key with
    any of them changed is a caller bug, not a retry.

    `occurred_at` must carry an offset. A naive timestamp would be silently
    interpreted as UTC, and a caller reporting local wall-clock time would have
    its month-boundary spend filed against the wrong period.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    department_id: DepartmentId | None = Field(default=None, min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    occurred_at: AwareDatetime
    logical_model_code: str = Field(min_length=1, max_length=100)
    run_id: UUID | None = None


class UsageDraft(ApiModel):
    """A usage record before a budget has been chosen for it.

    Which budget applies depends on what exists at the moment of the append, so
    the caller deliberately cannot name one.
    """

    tenant_id: UUID
    department_id: DepartmentId | None = Field(default=None, min_length=1, max_length=100)
    period: str = Field(pattern=MONTH_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    occurred_at: AwareDatetime
    logical_model_code: str = Field(min_length=1, max_length=100)
    run_id: UUID | None = None


class UsageEntry(Entity):
    """An append-only ledger row. Nothing rewrites one once it is stored."""

    budget_id: UUID
    department_id: DepartmentId | None = Field(default=None, min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    occurred_at: AwareDatetime
    period: str = Field(pattern=MONTH_PATTERN)
    logical_model_code: str = Field(min_length=1, max_length=100)
    run_id: UUID | None = None


class UsageDecision(StrEnum):
    RECORDED = "recorded"
    REPLAYED = "replayed"


class UsageReceipt(ApiModel):
    """What the ledger did, and where the period now stands.

    A blocked call never produces a receipt — it raises — so a caller cannot
    mistake "over budget" for a successful record by ignoring a field.
    """

    decision: UsageDecision
    entry: UsageEntry
    consumed: Decimal
    remaining: Decimal
    soft_limit: Decimal
    hard_limit: Decimal
    soft_limit_breached: bool
