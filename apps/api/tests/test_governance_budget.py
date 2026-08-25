"""Budget and usage-ledger behaviour.

The suite drives the service directly with `asyncio.run` rather than through a
transport: the module has no routes yet, and the properties worth pinning
(atomic accounting, idempotency, isolation) are domain properties.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.governance.errors import (
    BudgetCurrencyLockedError,
    BudgetExceededError,
    BudgetNotConfiguredError,
    CurrencyMismatchError,
    IdempotencyKeyReusedError,
    UnbudgetedSpendError,
)
from app.governance.repository import AppendStatus, InMemoryBudgetRepository
from app.governance.schemas import (
    BudgetRequest,
    BudgetScope,
    UsageDecision,
    UsageDraft,
    UsageRequest,
    billing_period,
)
from app.governance.service import GovernanceService, budget_precedence

MARCH = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
APRIL = datetime(2026, 4, 1, 0, 5, tzinfo=UTC)
DESIGN = "dept-design"


def service() -> GovernanceService:
    return GovernanceService(InMemoryBudgetRepository())


def tenant_budget(
    period: str = "2026-03", *, soft: str = "80", hard: str = "100", currency: str = "USD"
) -> BudgetRequest:
    return BudgetRequest(
        scope=BudgetScope.TENANT,
        period=period,
        currency=currency,
        soft_limit=Decimal(soft),
        hard_limit=Decimal(hard),
    )


def department_budget(
    department_id: str = DESIGN, period: str = "2026-03", *, soft: str = "5", hard: str = "10"
) -> BudgetRequest:
    return BudgetRequest(
        scope=BudgetScope.DEPARTMENT,
        department_id=department_id,
        period=period,
        currency="USD",
        soft_limit=Decimal(soft),
        hard_limit=Decimal(hard),
    )


def usage(
    amount: str,
    *,
    key: str | None = None,
    department_id: str | None = None,
    moment: datetime = MARCH,
    currency: str = "USD",
    model: str = "img-general-v1",
    run_id=None,
) -> UsageRequest:
    return UsageRequest(
        idempotency_key=key or f"call-{uuid4()}",
        department_id=department_id,
        amount=Decimal(amount),
        currency=currency,
        occurred_at=moment,
        logical_model_code=model,
        run_id=run_id,
    )


def test_usage_accumulates_and_reports_headroom() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())

        first = await governance.record_usage(tenant, usage("30"))
        second = await governance.record_usage(tenant, usage("25"))

        assert first.decision is UsageDecision.RECORDED
        assert first.consumed == Decimal("30")
        assert second.consumed == Decimal("55")
        assert second.remaining == Decimal("45")
        assert second.soft_limit_breached is False
        assert second.entry.period == "2026-03"

    asyncio.run(scenario())


def test_soft_limit_warns_without_blocking() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(soft="80", hard="100"))

        below = await governance.record_usage(tenant, usage("79.999999"))
        assert below.soft_limit_breached is False

        # Crossing the soft limit is an alert, not a refusal: the call is still
        # recorded and headroom remains.
        crossing = await governance.record_usage(tenant, usage("0.000001"))
        assert crossing.soft_limit_breached is True
        assert crossing.consumed == Decimal("80")
        assert crossing.remaining == Decimal("20")

    asyncio.run(scenario())


def test_hard_limit_blocks_and_records_nothing() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())
        await governance.record_usage(tenant, usage("95"))

        with pytest.raises(BudgetExceededError) as blocked:
            await governance.record_usage(tenant, usage("10"))

        assert blocked.value.error_code == "budget_exhausted"
        assert blocked.value.errors[0]["remaining"] == "5"

        _, consumed = await governance.status(tenant, None, MARCH)
        assert consumed == Decimal("95")

        # The budget is not poisoned: anything that still fits is accepted.
        assert (await governance.record_usage(tenant, usage("5"))).consumed == Decimal("100")

    asyncio.run(scenario())


def test_repeating_a_request_charges_once() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())

        first = await governance.record_usage(tenant, usage("40", key="retry-me"))
        replay = await governance.record_usage(tenant, usage("40", key="retry-me"))

        assert first.decision is UsageDecision.RECORDED
        assert replay.decision is UsageDecision.REPLAYED
        assert replay.entry.id == first.entry.id
        assert replay.consumed == Decimal("40")

    asyncio.run(scenario())


def test_a_retry_is_not_double_charged_when_a_department_budget_appears() -> None:
    """Regression: idempotency is per tenant, not per budget.

    The first attempt falls back to the tenant budget. A department budget then
    appears, so the retry resolves somewhere else — and a per-budget key index
    would see a fresh key and charge the same call a second time.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())
        call = usage("6", key="same-call", department_id=DESIGN)

        first = await governance.record_usage(tenant, call)
        await governance.set_budget(tenant, department_budget())
        retry = await governance.record_usage(tenant, call)

        assert retry.decision is UsageDecision.REPLAYED
        assert retry.entry.id == first.entry.id
        assert retry.entry.budget_id == first.entry.budget_id

        # Charged once, and only to the budget that took it originally.
        tenant_budget_row, tenant_spend = await governance.status(tenant, None, MARCH)
        _, department_spend = await governance.status(tenant, DESIGN, MARCH)
        assert tenant_spend == Decimal("6")
        assert department_spend == Decimal("0")
        assert retry.entry.budget_id == tenant_budget_row.id

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "variant",
    [
        {"amount": "41"},
        {"currency": "CNY"},
        {"model": "img-hd-v2"},
        {"run_id": uuid4()},
        {"department_id": DESIGN},
        {"moment": datetime(2026, 3, 14, 9, 31, tzinfo=UTC)},
    ],
)
def test_reusing_a_key_for_a_different_request_conflicts(variant: dict) -> None:
    """A changed field means it is a different call, not a retry.

    Replaying the first answer would silently drop the second charge, which is
    the failure mode idempotency is supposed to prevent.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())
        await governance.set_budget(tenant, department_budget(hard="50", soft="40"))
        await governance.record_usage(tenant, usage("40", key="shared"))

        with pytest.raises(IdempotencyKeyReusedError) as reused:
            await governance.record_usage(tenant, usage(key="shared", **{"amount": "40", **variant}))
        assert reused.value.status_code == 409

        _, consumed = await governance.status(tenant, None, MARCH)
        assert consumed == Decimal("40")

    asyncio.run(scenario())


def test_concurrent_calls_cannot_overshoot_the_hard_limit() -> None:
    """Pins the observable guarantee, not the mechanism.

    The in-memory store's critical section contains no await, so this would pass
    even with its lock removed — asyncio cannot interleave straight-line code.
    The lock earns its keep once the section spans a real await, which is what
    the PostgreSQL implementation will be.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(hard="100", soft="100"))

        outcomes = await asyncio.gather(
            *(governance.record_usage(tenant, usage("10")) for _ in range(20)),
            return_exceptions=True,
        )

        accepted = [item for item in outcomes if not isinstance(item, BaseException)]
        blocked = [item for item in outcomes if isinstance(item, BudgetExceededError)]
        assert len(accepted) == 10
        assert len(blocked) == 10

        _, consumed = await governance.status(tenant, None, MARCH)
        assert consumed == Decimal("100")

    asyncio.run(scenario())


def test_concurrent_retries_of_one_request_charge_once() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())

        outcomes = await asyncio.gather(
            *(governance.record_usage(tenant, usage("7", key="one-call")) for _ in range(20))
        )

        assert {outcome.entry.id for outcome in outcomes} == {outcomes[0].entry.id}
        _, consumed = await governance.status(tenant, None, MARCH)
        assert consumed == Decimal("7")

    asyncio.run(scenario())


def test_a_limit_lowered_concurrently_is_the_one_enforced() -> None:
    """Regression: the limit must be read inside the same atomic step.

    A caller that resolved the budget while the cap was 100 must not be admitted
    against 100 once the cap is 50 — the append has to re-read the current row.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(hard="100", soft="100"))

        lower, spend = await asyncio.gather(
            governance.set_budget(tenant, tenant_budget(hard="50", soft="50")),
            governance.record_usage(tenant, usage("60")),
            return_exceptions=True,
        )
        assert not isinstance(lower, BaseException)

        _, consumed = await governance.status(tenant, None, MARCH)
        if isinstance(spend, BudgetExceededError):
            # The lowered cap won the race and was enforced against it.
            assert spend.errors[0]["hardLimit"] == "50"
            assert consumed == Decimal("0")
        else:
            # The spend landed first; the new cap only binds what comes next.
            assert spend.consumed == Decimal("60")
            assert consumed == Decimal("60")
            with pytest.raises(BudgetExceededError):
                await governance.record_usage(tenant, usage("1"))

    asyncio.run(scenario())


def draft(
    tenant,
    amount: str,
    *,
    key: str,
    department_id: str | None = None,
    currency: str = "USD",
) -> UsageDraft:
    return UsageDraft(
        tenant_id=tenant,
        department_id=department_id,
        period="2026-03",
        idempotency_key=key,
        amount=Decimal(amount),
        currency=currency,
        occurred_at=MARCH,
        logical_model_code="img-general-v1",
    )


def test_append_enforces_the_current_limit_not_the_caller_s() -> None:
    """The discriminating case for the stale-limit bug.

    The concurrent test above cannot separate old from new behaviour — both
    orderings look the same from outside. This one pins the mechanism: the cap
    changed after the caller could have read it, and the append must enforce the
    value in force at the moment it runs.
    """

    async def scenario() -> None:
        repository = InMemoryBudgetRepository()
        governance = GovernanceService(repository)
        tenant = uuid4()
        budget = await governance.set_budget(tenant, tenant_budget(hard="100", soft="100"))
        await governance.set_budget(tenant, tenant_budget(hard="50", soft="50"))

        outcome = await repository.append_usage(
            draft(tenant, "60", key="late-arrival"),
            candidates=budget_precedence(None),
            fingerprint="fp",
        )

        assert outcome.status is AppendStatus.REJECTED
        assert outcome.budget is not None and outcome.budget.hard_limit == Decimal("50")
        assert outcome.entry is None
        assert await repository.entries(tenant, budget.id) == []

    asyncio.run(scenario())


def test_append_resolves_the_budget_at_the_moment_it_runs() -> None:
    """The discriminating case for the stale-scope bug.

    Choosing the budget before the append means a department budget created in
    the meantime is ignored and the tenant is charged instead. Resolution has to
    happen inside the same step as the append, against current data.
    """

    async def scenario() -> None:
        repository = InMemoryBudgetRepository()
        governance = GovernanceService(repository)
        tenant = uuid4()
        tenant_row = await governance.set_budget(tenant, tenant_budget())
        candidates = budget_precedence(DESIGN)

        fell_back = await repository.append_usage(
            draft(tenant, "3", key="before", department_id=DESIGN),
            candidates=candidates,
            fingerprint="fp-before",
        )
        assert fell_back.budget is not None and fell_back.budget.id == tenant_row.id

        department_row = await governance.set_budget(tenant, department_budget())
        honoured = await repository.append_usage(
            draft(tenant, "3", key="after", department_id=DESIGN),
            candidates=candidates,
            fingerprint="fp-after",
        )

        # Same candidate list, different answer: the budget that appeared in
        # between is honoured rather than silently skipped.
        assert honoured.budget is not None and honoured.budget.id == department_row.id
        assert await repository.consumed(tenant, tenant_row.id) == Decimal("3")
        assert await repository.consumed(tenant, department_row.id) == Decimal("3")

    asyncio.run(scenario())


def test_a_departmental_charge_never_lands_on_both_budgets() -> None:
    """Whichever way the race falls, the call is charged exactly once."""

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        tenant_row = await governance.set_budget(tenant, tenant_budget())

        created, receipt = await asyncio.gather(
            governance.set_budget(tenant, department_budget(hard="50", soft="40")),
            governance.record_usage(tenant, usage("8", department_id=DESIGN)),
        )

        _, tenant_spend = await governance.status(tenant, None, MARCH)
        _, department_spend = await governance.status(tenant, DESIGN, MARCH)
        assert tenant_spend + department_spend == Decimal("8")
        assert receipt.entry.budget_id in {tenant_row.id, created.id}

    asyncio.run(scenario())


def test_revising_a_budget_keeps_its_creation_time() -> None:
    """Regression: an update revises a budget, it does not replace it.

    Rewriting `created_at` on every edit would make the budget look as though it
    came into existence in whatever month it was last touched.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        original = await governance.set_budget(tenant, tenant_budget(hard="100"))
        revised = await governance.set_budget(tenant, tenant_budget(hard="200"))

        assert revised.id == original.id
        assert revised.created_at == original.created_at
        assert revised.updated_at >= original.updated_at
        assert revised.hard_limit == Decimal("200")

    asyncio.run(scenario())


def test_department_budget_takes_precedence_over_the_tenant_budget() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())
        await governance.set_budget(tenant, department_budget(hard="10", soft="5"))

        receipt = await governance.record_usage(tenant, usage("6", department_id=DESIGN))
        assert receipt.hard_limit == Decimal("10")
        assert receipt.soft_limit_breached is True

        with pytest.raises(BudgetExceededError):
            await governance.record_usage(tenant, usage("5", department_id=DESIGN))

        # The tenant budget is a fallback, not a tenant-wide ceiling: department
        # spend is not also deducted from it.
        _, tenant_consumed = await governance.status(tenant, None, MARCH)
        assert tenant_consumed == Decimal("0")

    asyncio.run(scenario())


def test_a_department_without_its_own_budget_falls_back_to_the_tenant() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())

        receipt = await governance.record_usage(tenant, usage("12", department_id="dept-sales"))
        assert receipt.hard_limit == Decimal("100")
        assert receipt.consumed == Decimal("12")

    asyncio.run(scenario())


def test_budgets_and_spend_are_isolated_per_tenant() -> None:
    async def scenario() -> None:
        governance = service()
        tenant_a = uuid4()
        tenant_b = uuid4()
        await governance.set_budget(tenant_a, tenant_budget())
        await governance.record_usage(tenant_a, usage("60", key="shared-key"))

        with pytest.raises(UnbudgetedSpendError):
            await governance.record_usage(tenant_b, usage("1"))

        await governance.set_budget(tenant_b, tenant_budget())
        # The same key in another tenant is a different command entirely.
        receipt = await governance.record_usage(tenant_b, usage("1", key="shared-key"))
        assert receipt.decision is UsageDecision.RECORDED
        assert receipt.consumed == Decimal("1")

        _, spend_a = await governance.status(tenant_a, None, MARCH)
        assert spend_a == Decimal("60")

    asyncio.run(scenario())


def test_each_month_gets_its_own_quota() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget("2026-03"))
        await governance.set_budget(tenant, tenant_budget("2026-04"))
        await governance.record_usage(tenant, usage("90", moment=MARCH))

        # A record is filed by when it was incurred, so April starts empty even
        # though March is nearly exhausted.
        april = await governance.record_usage(tenant, usage("90", moment=APRIL))
        assert april.entry.period == "2026-04"
        assert april.consumed == Decimal("90")

    asyncio.run(scenario())


def test_unbudgeted_spend_is_refused_as_a_conflict() -> None:
    async def scenario() -> None:
        governance = service()
        with pytest.raises(UnbudgetedSpendError) as refused:
            await governance.record_usage(uuid4(), usage("1"))
        assert refused.value.status_code == 409
        assert refused.value.error_code == "budget_not_configured"

    asyncio.run(scenario())


def test_looking_up_a_missing_budget_is_a_not_found() -> None:
    async def scenario() -> None:
        governance = service()
        with pytest.raises(BudgetNotConfiguredError) as missing:
            await governance.budget_for(uuid4(), None, "2026-03")
        assert missing.value.status_code == 404

    asyncio.run(scenario())


def test_usage_in_another_currency_is_refused() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget())

        with pytest.raises(CurrencyMismatchError):
            await governance.record_usage(tenant, usage("1", currency="CNY"))

    asyncio.run(scenario())


def test_a_charged_budget_cannot_be_re_denominated() -> None:
    """Regression: otherwise USD and CNY are summed into one meaningless total."""

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(currency="USD"))
        await governance.record_usage(tenant, usage("10", currency="USD"))

        with pytest.raises(BudgetCurrencyLockedError) as locked:
            await governance.set_budget(tenant, tenant_budget(currency="CNY"))
        assert locked.value.status_code == 409

        budget, consumed = await governance.status(tenant, None, MARCH)
        assert budget.currency == "USD"
        assert consumed == Decimal("10")

    asyncio.run(scenario())


def test_an_untouched_budget_may_still_be_re_denominated() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(currency="USD"))
        redenominated = await governance.set_budget(tenant, tenant_budget(currency="CNY"))
        assert redenominated.currency == "CNY"

    asyncio.run(scenario())


def test_lowering_a_limit_blocks_new_spend_but_keeps_history() -> None:
    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget(hard="100", soft="80"))
        await governance.record_usage(tenant, usage("60"))

        await governance.set_budget(tenant, tenant_budget(hard="50", soft="40"))

        budget, consumed = await governance.status(tenant, None, MARCH)
        assert budget.hard_limit == Decimal("50")
        assert consumed == Decimal("60")

        with pytest.raises(BudgetExceededError):
            await governance.record_usage(tenant, usage("1"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("moment", "period"),
    [
        # The last instant of March in UTC is still March.
        (datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC), "2026-03"),
        (datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC), "2026-04"),
        # Already April on a Shanghai calendar, still March as an instant.
        (datetime(2026, 4, 1, 7, 0, tzinfo=timezone(timedelta(hours=8))), "2026-03"),
        (datetime(2026, 4, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))), "2026-04"),
        # Still March in Los Angeles, already April as an instant.
        (datetime(2026, 3, 31, 18, 0, tzinfo=timezone(timedelta(hours=-7))), "2026-04"),
        (datetime(2026, 3, 31, 16, 0, tzinfo=timezone(timedelta(hours=-7))), "2026-03"),
    ],
)
def test_the_period_is_the_month_of_the_instant_in_utc(moment: datetime, period: str) -> None:
    assert billing_period(moment) == period


def test_a_month_boundary_is_charged_by_instant_not_by_local_calendar() -> None:
    """Two reporters, two calendars, one instant — one period.

    Without the UTC conversion these two land in different months, and a tenant
    could get a fresh quota by reporting from a different offset.
    """

    async def scenario() -> None:
        governance = service()
        tenant = uuid4()
        await governance.set_budget(tenant, tenant_budget("2026-03", hard="100", soft="100"))

        shanghai = datetime(2026, 4, 1, 7, 0, tzinfo=timezone(timedelta(hours=8)))
        los_angeles = datetime(2026, 3, 31, 16, 0, tzinfo=timezone(timedelta(hours=-7)))

        first = await governance.record_usage(tenant, usage("60", moment=shanghai))
        second = await governance.record_usage(tenant, usage("40", moment=los_angeles))

        assert first.entry.period == "2026-03"
        assert second.entry.period == "2026-03"
        assert second.consumed == Decimal("100")

        # April has no budget of its own, so the next instant is unbudgeted
        # rather than quietly starting a second quota.
        with pytest.raises(UnbudgetedSpendError):
            await governance.record_usage(
                tenant, usage("1", moment=datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
            )

    asyncio.run(scenario())


def test_a_naive_timestamp_is_rejected() -> None:
    naive = datetime(2026, 3, 14, 9, 30)

    with pytest.raises(ValidationError):
        UsageRequest(
            idempotency_key="naive",
            amount=Decimal("1"),
            currency="USD",
            occurred_at=naive,
            logical_model_code="img-general-v1",
        )
    with pytest.raises(ValidationError):
        UsageDraft(
            tenant_id=uuid4(),
            period="2026-03",
            idempotency_key="naive",
            amount=Decimal("1"),
            currency="USD",
            occurred_at=naive,
            logical_model_code="img-general-v1",
        )


def test_an_incoherent_budget_is_rejected() -> None:
    headless = BudgetRequest(
        scope=BudgetScope.DEPARTMENT,
        period="2026-03",
        currency="USD",
        soft_limit=Decimal("1"),
        hard_limit=Decimal("2"),
    )
    inverted = BudgetRequest(
        scope=BudgetScope.TENANT,
        period="2026-03",
        currency="USD",
        soft_limit=Decimal("9"),
        hard_limit=Decimal("2"),
    )

    with pytest.raises(ValidationError):
        asyncio.run(service().set_budget(uuid4(), headless))
    with pytest.raises(ValidationError):
        asyncio.run(service().set_budget(uuid4(), inverted))
