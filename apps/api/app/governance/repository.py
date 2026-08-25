"""Budget storage and the append-only usage ledger.

`append_usage` is the whole point of this module. Deduplicating the command,
choosing which budget applies, reading that budget's *current* limit, comparing
it against the running total and appending must be one indivisible step. Take
any of it outside and the ledger loses money: a limit lowered in between is not
enforced, a department budget created in between is not honoured, and two
concurrent calls both read "under budget" and both append.

That is why the caller hands over candidates rather than a chosen budget. The
precedence between them is policy and belongs to the service; applying it at the
instant of the append is atomicity and belongs here — as do the integrity
invariants that need the same instant: tenant ownership, currency coherence and
idempotency.

A PostgreSQL implementation gets most of the way there with one `SELECT … FOR
UPDATE` over the candidate rows, ordered by precedence, plus a unique index on
`(tenant_id, idempotency_key)`, inside the transaction that inserts the entry.

That is not sufficient on its own. Row locks cannot lock a row that does not
exist yet, so a department budget inserted concurrently is invisible to the
`SELECT` and the charge still lands on the tenant. Closing that needs one of: an
advisory lock on the logical key `(tenant_id, department_id, period)`,
`SERIALIZABLE` isolation with retry, or locking the tenant-budget row as the
parent so any insert beneath it serialises behind the same lock.
"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from app.governance.errors import (
    BudgetCurrencyLockedError,
    CurrencyMismatchError,
    IdempotencyKeyReusedError,
)
from app.governance.schemas import (
    Budget,
    BudgetScope,
    DepartmentId,
    UsageDraft,
    UsageEntry,
)


@dataclass(frozen=True, slots=True)
class BudgetCandidate:
    """One budget the caller would accept, identified by scope."""

    scope: BudgetScope
    department_id: DepartmentId | None


class AppendStatus(StrEnum):
    RECORDED = "recorded"
    REPLAYED = "replayed"
    REJECTED = "rejected"
    """Over the hard limit. Nothing was stored."""

    UNBUDGETED = "unbudgeted"
    """No candidate budget existed. Nothing was stored."""


@dataclass(frozen=True, slots=True)
class AppendOutcome:
    status: AppendStatus

    budget: Budget | None
    """The budget that was resolved, with the limits actually enforced.

    None only when nothing matched. Callers must build their answer from this
    rather than from anything they read beforehand, which may already be stale.
    """

    entry: UsageEntry | None
    """The stored row — present only for RECORDED and REPLAYED.

    A rejected append has no entry at all: surfacing the candidate here would
    read as though it had been written.
    """

    consumed: Decimal


@dataclass(frozen=True, slots=True)
class _BudgetKey:
    tenant_id: UUID
    scope: BudgetScope
    department_id: DepartmentId | None
    period: str


@dataclass(frozen=True, slots=True)
class _Command:
    entry: UsageEntry
    fingerprint: str


class BudgetRepository(Protocol):
    async def upsert_budget(self, budget: Budget) -> Budget: ...

    async def find_budget(
        self,
        tenant_id: UUID,
        *,
        scope: BudgetScope,
        department_id: DepartmentId | None,
        period: str,
    ) -> Budget | None: ...

    async def consumed(self, tenant_id: UUID, budget_id: UUID) -> Decimal: ...

    async def entries(self, tenant_id: UUID, budget_id: UUID) -> list[UsageEntry]: ...

    async def append_usage(
        self,
        draft: UsageDraft,
        *,
        candidates: Sequence[BudgetCandidate],
        fingerprint: str,
    ) -> AppendOutcome: ...


class InMemoryBudgetRepository:
    """Development store with tenant isolation and copy-on-read semantics.

    One lock covers every mutation. Finer-grained locking would buy nothing here
    and costs an acquisition-order invariant to get wrong; the store this stands
    in for will use row locks instead.
    """

    def __init__(self) -> None:
        self._budgets: dict[_BudgetKey, Budget] = {}
        self._by_id: dict[UUID, Budget] = {}
        self._entries: dict[UUID, list[UsageEntry]] = defaultdict(list)
        self._totals: dict[UUID, Decimal] = defaultdict(Decimal)
        self._commands: dict[tuple[UUID, str], _Command] = {}
        self._lock = asyncio.Lock()

    async def upsert_budget(self, budget: Budget) -> Budget:
        key = _BudgetKey(budget.tenant_id, budget.scope, budget.department_id, budget.period)
        async with self._lock:
            existing = self._budgets.get(key)
            if existing is not None:
                if self._entries[existing.id] and existing.currency != budget.currency:
                    raise BudgetCurrencyLockedError(
                        current=existing.currency, requested=budget.currency
                    )
                # An update revises the limits of the budget that is already
                # there; it does not create a new one, so its identity and the
                # moment it came into existence both survive.
                budget = budget.model_copy(
                    update={"id": existing.id, "created_at": existing.created_at}
                )
            self._budgets[key] = budget.model_copy(deep=True)
            self._by_id[budget.id] = budget.model_copy(deep=True)
        return budget.model_copy(deep=True)

    async def find_budget(
        self,
        tenant_id: UUID,
        *,
        scope: BudgetScope,
        department_id: DepartmentId | None,
        period: str,
    ) -> Budget | None:
        budget = self._budgets.get(_BudgetKey(tenant_id, scope, department_id, period))
        return None if budget is None else budget.model_copy(deep=True)

    async def consumed(self, tenant_id: UUID, budget_id: UUID) -> Decimal:
        if not self._owns(tenant_id, budget_id):
            return Decimal(0)
        return self._totals[budget_id]

    async def entries(self, tenant_id: UUID, budget_id: UUID) -> list[UsageEntry]:
        if not self._owns(tenant_id, budget_id):
            return []
        return [entry.model_copy(deep=True) for entry in self._entries[budget_id]]

    async def append_usage(
        self,
        draft: UsageDraft,
        *,
        candidates: Sequence[BudgetCandidate],
        fingerprint: str,
    ) -> AppendOutcome:
        async with self._lock:
            command_key = (draft.tenant_id, draft.idempotency_key)
            seen = self._commands.get(command_key)
            if seen is not None:
                if seen.fingerprint != fingerprint:
                    raise IdempotencyKeyReusedError(draft.idempotency_key)
                # Deliberately the budget the original was charged to. A budget
                # that appeared between the two attempts must not route the
                # retry elsewhere and charge the same call twice.
                charged = self._by_id[seen.entry.budget_id]
                return AppendOutcome(
                    status=AppendStatus.REPLAYED,
                    budget=charged.model_copy(deep=True),
                    entry=seen.entry.model_copy(deep=True),
                    consumed=self._totals[charged.id],
                )

            budget = self._first_match(draft, candidates)
            if budget is None:
                return AppendOutcome(
                    status=AppendStatus.UNBUDGETED,
                    budget=None,
                    entry=None,
                    consumed=Decimal(0),
                )
            if budget.currency != draft.currency:
                raise CurrencyMismatchError(expected=budget.currency, actual=draft.currency)

            consumed = self._totals[budget.id]
            if consumed + draft.amount > budget.hard_limit:
                return AppendOutcome(
                    status=AppendStatus.REJECTED,
                    budget=budget.model_copy(deep=True),
                    entry=None,
                    consumed=consumed,
                )

            now = datetime.now(UTC)
            stored = UsageEntry(
                id=uuid4(),
                tenant_id=draft.tenant_id,
                budget_id=budget.id,
                department_id=draft.department_id,
                idempotency_key=draft.idempotency_key,
                amount=draft.amount,
                currency=draft.currency,
                occurred_at=draft.occurred_at,
                period=draft.period,
                logical_model_code=draft.logical_model_code,
                run_id=draft.run_id,
                created_at=now,
                updated_at=now,
            )
            self._entries[budget.id].append(stored)
            self._commands[command_key] = _Command(entry=stored, fingerprint=fingerprint)
            self._totals[budget.id] = consumed + draft.amount
            return AppendOutcome(
                status=AppendStatus.RECORDED,
                budget=budget.model_copy(deep=True),
                entry=stored.model_copy(deep=True),
                consumed=self._totals[budget.id],
            )

    def _first_match(
        self, draft: UsageDraft, candidates: Sequence[BudgetCandidate]
    ) -> Budget | None:
        for candidate in candidates:
            budget = self._budgets.get(
                _BudgetKey(
                    draft.tenant_id, candidate.scope, candidate.department_id, draft.period
                )
            )
            if budget is not None:
                return budget
        return None

    def _owns(self, tenant_id: UUID, budget_id: UUID) -> bool:
        budget = self._by_id.get(budget_id)
        return budget is not None and budget.tenant_id == tenant_id
