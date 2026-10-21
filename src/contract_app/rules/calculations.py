"""Exact, candidate-only arithmetic for extracted payment schedules."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_DECIMAL = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_DECIMAL_LENGTH = 128
_MAX_SCHEDULE_ITEMS = 500


class CalculationInputInvalid(ValueError):
    """A payment candidate cannot be safely processed by the decimal executor."""


@dataclass(frozen=True, slots=True)
class PaymentTotals:
    """Exact arithmetic over extracted values; never a contract judgment.

    `percentage_value_sum` preserves the source's numeric scale; it is not
    normalized to a fraction or converted into a monetary amount.
    """

    percentage_value_sum: str
    fixed_amount_totals: tuple[tuple[str, str], ...]
    residual_installments: int


@dataclass(frozen=True, slots=True)
class PriceBasisCheck:
    """Exact arithmetic for contract-price basis candidates."""

    currency: str
    tax_difference: str | None
    tax_consistent: bool | None
    fixed_amount: str | None
    percentage_amount: str | None
    fixed_percentage_consistent: bool | None
    preferred_amount: str | None


def sum_decimal_strings(values: Sequence[str]) -> str:
    """Sum non-negative plain decimal strings without context rounding."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise CalculationInputInvalid("calculation_values_invalid")
    if len(values) > _MAX_SCHEDULE_ITEMS:
        raise CalculationInputInvalid("calculation_item_limit_exceeded")
    parsed: list[tuple[int, int]] = []
    max_scale = 0
    for value in values:
        if (
            not isinstance(value, str)
            or len(value) > _MAX_DECIMAL_LENGTH
            or _DECIMAL.fullmatch(value) is None
        ):
            raise CalculationInputInvalid("calculation_decimal_invalid")
        whole, separator, fraction = value.partition(".")
        scale = len(fraction) if separator else 0
        coefficient = int(f"{whole}{fraction}")
        parsed.append((coefficient, scale))
        max_scale = max(max_scale, scale)

    total = sum(coefficient * 10 ** (max_scale - scale) for coefficient, scale in parsed)
    return _format_decimal(total, max_scale)


def execute_calculation_tool(tool_id: str, arguments: Mapping[str, Any]) -> str:
    """Execute the closed calculation-tool registry used by compiled rules."""
    if tool_id != "calculation.sum_decimal":
        raise CalculationInputInvalid("calculation_tool_not_implemented")
    if not isinstance(arguments, Mapping) or set(arguments) != {"values"}:
        raise CalculationInputInvalid("calculation_tool_arguments_invalid")
    return sum_decimal_strings(arguments["values"])


def calculate_payment_totals(schedule: Sequence[Mapping[str, Any]]) -> PaymentTotals:
    """Aggregate explicit percentages and same-currency fixed amounts only.

    Residual installments are counted but not assigned a value. No currency
    conversion, schedule interpretation, or risk threshold is inferred.
    """
    if not isinstance(schedule, Sequence) or isinstance(schedule, (str, bytes)):
        raise CalculationInputInvalid("payment_schedule_invalid")
    if len(schedule) > _MAX_SCHEDULE_ITEMS:
        raise CalculationInputInvalid("calculation_item_limit_exceeded")

    percentages: list[str] = []
    fixed_by_currency: dict[str, list[str]] = {}
    residual_installments = 0
    for item in schedule:
        if not isinstance(item, Mapping):
            raise CalculationInputInvalid("payment_schedule_item_invalid")
        amount_type = item.get("amount_type")
        if amount_type == "percentage":
            percentages.append(_required_decimal(item.get("percentage")))
        elif amount_type == "fixed":
            amount = _required_decimal(item.get("amount"))
            currency = item.get("currency")
            if not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None:
                raise CalculationInputInvalid("fixed_payment_currency_invalid")
            fixed_by_currency.setdefault(currency, []).append(amount)
        elif amount_type == "residual":
            residual_installments += 1
        else:
            raise CalculationInputInvalid("payment_amount_type_invalid")

    return PaymentTotals(
        percentage_value_sum=execute_calculation_tool(
            "calculation.sum_decimal", {"values": percentages}
        ),
        fixed_amount_totals=tuple(
            (
                currency,
                execute_calculation_tool("calculation.sum_decimal", {"values": amounts}),
            )
            for currency, amounts in sorted(fixed_by_currency.items())
        ),
        residual_installments=residual_installments,
    )


def check_price_basis(
    *,
    tax_inclusive_total: str | None,
    tax_exclusive_total: str | None,
    tax_amount: str | None = None,
    currency: str | None = None,
    fixed_amount: str | None = None,
    percentage: str | None = None,
    denominator: str | None = None,
) -> PriceBasisCheck:
    """Compare explicit price values without inventing missing values.

    Percentages are decimal percentages (``30`` means 30%), and the
    denominator is a monetary total supplied by the caller. When both amount
    forms exist, the fixed amount remains the preferred value.
    """
    resolved_currency = currency or "CNY"
    if _CURRENCY.fullmatch(resolved_currency) is None:
        raise CalculationInputInvalid("price_currency_invalid")
    tax_difference: str | None = None
    tax_consistent: bool | None = None
    if tax_inclusive_total is not None and tax_exclusive_total is not None:
        inclusive, inclusive_scale = _decimal_parts(tax_inclusive_total)
        exclusive, exclusive_scale = _decimal_parts(tax_exclusive_total)
        tax_scale = max(inclusive_scale, exclusive_scale)
        difference = inclusive * 10 ** (tax_scale - inclusive_scale) - exclusive * 10 ** (
            tax_scale - exclusive_scale
        )
        tax_difference = _format_signed_decimal(difference, tax_scale)
        if tax_amount is not None:
            tax_consistent = tax_difference == _normalize_decimal(tax_amount)
    fixed_value = _normalize_decimal(fixed_amount) if fixed_amount is not None else None
    percentage_value: str | None = None
    fixed_percentage_consistent: bool | None = None
    if percentage is not None and denominator is not None:
        ratio, ratio_scale = _decimal_parts(percentage)
        total, total_scale = _decimal_parts(denominator)
        percentage_value = _format_decimal(ratio * total, ratio_scale + total_scale + 2)
        if fixed_value is not None:
            fixed_percentage_consistent = fixed_value == percentage_value
    return PriceBasisCheck(
        currency=resolved_currency,
        tax_difference=tax_difference,
        tax_consistent=tax_consistent,
        fixed_amount=fixed_value,
        percentage_amount=percentage_value,
        fixed_percentage_consistent=fixed_percentage_consistent,
        preferred_amount=fixed_value or percentage_value,
    )


def _required_decimal(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_DECIMAL_LENGTH
        or _DECIMAL.fullmatch(value) is None
    ):
        raise CalculationInputInvalid("calculation_decimal_invalid")
    return value


def _decimal_parts(value: str) -> tuple[int, int]:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_DECIMAL_LENGTH
        or _DECIMAL.fullmatch(value) is None
    ):
        raise CalculationInputInvalid("calculation_decimal_invalid")
    whole, separator, fraction = value.partition(".")
    return int(f"{whole}{fraction}"), len(fraction) if separator else 0


def _normalize_decimal(value: str) -> str:
    coefficient, scale = _decimal_parts(value)
    return _format_decimal(coefficient, scale)


def _format_signed_decimal(coefficient: int, scale: int) -> str:
    if coefficient < 0:
        return f"-{_format_decimal(-coefficient, scale)}"
    return _format_decimal(coefficient, scale)


def _format_decimal(coefficient: int, scale: int) -> str:
    digits = str(coefficient).rjust(scale + 1, "0")
    if scale == 0:
        return digits
    whole, fraction = digits[:-scale], digits[-scale:]
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole
