from decimal import Decimal

from app.core.errors import DomainError


class BudgetNotConfiguredError(DomainError):
    """Asked for a budget that does not exist. A lookup miss, nothing more."""

    def __init__(self, period: str) -> None:
        super().__init__(
            title="Budget not configured",
            detail=f"No budget is configured for period '{period}'",
            status_code=404,
            error_code="budget_not_configured",
        )


class UnbudgetedSpendError(DomainError):
    """Spend arrived with no budget to charge it to, so it is refused.

    Fail-closed is the point: falling through to "unlimited" would make an
    unconfigured tenant the cheapest way to bypass governance. This is a 409
    rather than a 404 because the thing that is missing is not the resource the
    caller addressed — the call itself cannot be admitted in the current state.
    """

    def __init__(self, period: str) -> None:
        super().__init__(
            title="Spend is not budgeted",
            detail=f"No budget covers period '{period}', so the usage cannot be recorded",
            status_code=409,
            error_code="budget_not_configured",
        )


class BudgetExceededError(DomainError):
    def __init__(self, *, requested: Decimal, consumed: Decimal, hard_limit: Decimal) -> None:
        super().__init__(
            title="Budget exhausted",
            detail=(
                f"Recording {requested} would take the period to "
                f"{consumed + requested}, above the hard limit of {hard_limit}"
            ),
            status_code=409,
            error_code="budget_exhausted",
            errors=[
                {
                    "requested": str(requested),
                    "consumed": str(consumed),
                    "hardLimit": str(hard_limit),
                    "remaining": str(hard_limit - consumed),
                }
            ],
        )


class CurrencyMismatchError(DomainError):
    def __init__(self, *, expected: str, actual: str) -> None:
        super().__init__(
            title="Currency mismatch",
            detail=f"The budget is denominated in {expected}, the usage in {actual}",
            status_code=422,
            error_code="budget_currency_mismatch",
        )


class BudgetCurrencyLockedError(DomainError):
    """A budget's currency is fixed once it has been charged.

    Re-denominating a budget that already holds entries would silently add two
    currencies into one total, and no later reconciliation can separate them.
    """

    def __init__(self, *, current: str, requested: str) -> None:
        super().__init__(
            title="Budget currency is locked",
            detail=(
                f"The budget already holds usage in {current} and cannot be "
                f"re-denominated to {requested}"
            ),
            status_code=409,
            error_code="budget_currency_locked",
        )


class IdempotencyKeyReusedError(DomainError):
    """Same key, different request. Replaying the first answer would hide a bug."""

    def __init__(self, key: str) -> None:
        super().__init__(
            title="Idempotency key reused",
            detail=f"Key '{key}' was already used for a different usage record",
            status_code=409,
            error_code="idempotency_key_reused",
        )
