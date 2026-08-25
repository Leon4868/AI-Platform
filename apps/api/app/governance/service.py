"""Budget policy: which budget a call is charged to, and whether it may proceed.

A call is charged to the *most specific* budget that exists — the department's
when the caller names one and it has a budget, the tenant's otherwise. The tenant
budget is a **fallback for unbudgeted departments, not a tenant-wide ceiling**:
spend charged to a department is not also deducted from it, so the tenant figure
does not represent total organisational spend. A budget tree is out of scope for
this phase; cascading deduction would reintroduce it and force an atomic write
across two budgets.

This module decides the precedence. It never decides *which* budget a recorded
call landed on — that is settled inside the ledger's atomic step, because a
budget created a moment earlier has to be honoured.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.idempotency import request_fingerprint
from app.governance.errors import (
    BudgetExceededError,
    BudgetNotConfiguredError,
    UnbudgetedSpendError,
)
from app.governance.repository import AppendStatus, BudgetCandidate, BudgetRepository
from app.governance.schemas import (
    Budget,
    BudgetRequest,
    BudgetScope,
    DepartmentId,
    UsageDecision,
    UsageDraft,
    UsageEntry,
    UsageReceipt,
    UsageRequest,
    billing_period,
)


def budget_precedence(department_id: DepartmentId | None) -> tuple[BudgetCandidate, ...]:
    """The budgets a call may be charged to, most specific first."""
    tenant = BudgetCandidate(scope=BudgetScope.TENANT, department_id=None)
    if department_id is None:
        return (tenant,)
    return (BudgetCandidate(scope=BudgetScope.DEPARTMENT, department_id=department_id), tenant)


class GovernanceService:
    def __init__(self, repository: BudgetRepository) -> None:
        self._repository = repository

    async def set_budget(self, tenant_id: UUID, payload: BudgetRequest) -> Budget:
        """Defines or revises the budget for one scope and month.

        Limits may be raised or lowered at any time; already-recorded usage is
        never rewritten, so lowering a limit below current spend simply blocks
        the next call rather than invalidating history. The currency is the one
        thing that cannot change once the budget has been charged.
        """
        now = datetime.now(UTC)
        return await self._repository.upsert_budget(
            Budget(
                id=uuid4(),
                tenant_id=tenant_id,
                scope=payload.scope,
                department_id=payload.department_id,
                period=payload.period,
                currency=payload.currency,
                soft_limit=payload.soft_limit,
                hard_limit=payload.hard_limit,
                created_at=now,
                updated_at=now,
            )
        )

    async def budget_for(
        self, tenant_id: UUID, department_id: DepartmentId | None, period: str
    ) -> Budget:
        """The budget a call in this scope would be charged to right now.

        A read, and stale the moment it returns — recording never relies on it.
        """
        for candidate in budget_precedence(department_id):
            budget = await self._repository.find_budget(
                tenant_id,
                scope=candidate.scope,
                department_id=candidate.department_id,
                period=period,
            )
            if budget is not None:
                return budget
        raise BudgetNotConfiguredError(period)

    async def record_usage(self, tenant_id: UUID, payload: UsageRequest) -> UsageReceipt:
        period = billing_period(payload.occurred_at)
        outcome = await self._repository.append_usage(
            UsageDraft(
                tenant_id=tenant_id,
                department_id=payload.department_id,
                period=period,
                idempotency_key=payload.idempotency_key,
                amount=payload.amount,
                currency=payload.currency,
                occurred_at=payload.occurred_at,
                logical_model_code=payload.logical_model_code,
                run_id=payload.run_id,
            ),
            candidates=budget_precedence(payload.department_id),
            fingerprint=request_fingerprint(payload),
        )

        if outcome.status is AppendStatus.UNBUDGETED:
            raise UnbudgetedSpendError(period)
        assert outcome.budget is not None
        if outcome.status is AppendStatus.REJECTED:
            raise BudgetExceededError(
                requested=payload.amount,
                consumed=outcome.consumed,
                hard_limit=outcome.budget.hard_limit,
            )

        assert outcome.entry is not None
        return _receipt(
            outcome.budget,
            outcome.entry,
            outcome.consumed,
            replayed=outcome.status is AppendStatus.REPLAYED,
        )

    async def status(
        self, tenant_id: UUID, department_id: DepartmentId | None, moment: datetime
    ) -> tuple[Budget, Decimal]:
        """Where a budget stands, for dashboards and pre-flight checks."""
        budget = await self.budget_for(tenant_id, department_id, billing_period(moment))
        return budget, await self._repository.consumed(tenant_id, budget.id)


def _receipt(
    budget: Budget, entry: UsageEntry, consumed: Decimal, *, replayed: bool
) -> UsageReceipt:
    return UsageReceipt(
        decision=UsageDecision.REPLAYED if replayed else UsageDecision.RECORDED,
        entry=entry,
        consumed=consumed,
        remaining=budget.hard_limit - consumed,
        soft_limit=budget.soft_limit,
        hard_limit=budget.hard_limit,
        soft_limit_breached=consumed >= budget.soft_limit,
    )
