import pytest

from contract_app.rules.calculations import (
    CalculationInputInvalid,
    calculate_payment_totals,
    check_price_basis,
    execute_calculation_tool,
    sum_decimal_strings,
)


def test_decimal_sum_is_exact_and_canonical() -> None:
    assert sum_decimal_strings(["0.1", "0.20", "0001.00"]) == "1.3"
    assert sum_decimal_strings(["9999999999999999999999999999", "1"]) == (
        "10000000000000000000000000000"
    )
    assert sum_decimal_strings([]) == "0"


def test_sum_decimal_tool_is_closed_and_exact() -> None:
    assert execute_calculation_tool("calculation.sum_decimal", {"values": ["0.10", "0.2"]}) == "0.3"
    with pytest.raises(CalculationInputInvalid, match="calculation_tool_not_implemented"):
        execute_calculation_tool("calculation.payment_ratio", {"values": ["1"]})
    with pytest.raises(CalculationInputInvalid, match="calculation_tool_arguments_invalid"):
        execute_calculation_tool("calculation.sum_decimal", {"values": ["1"], "currency": "CNY"})


def test_payment_totals_keep_percentages_and_currencies_separate() -> None:
    totals = calculate_payment_totals(
        [
            {"amount_type": "percentage", "percentage": "20.5"},
            {"amount_type": "percentage", "percentage": "79.50"},
            {"amount_type": "fixed", "amount": "120.00", "currency": "CNY"},
            {"amount_type": "fixed", "amount": "30", "currency": "CNY"},
            {"amount_type": "fixed", "amount": "10", "currency": "USD"},
            {"amount_type": "residual"},
        ]
    )

    assert totals.percentage_value_sum == "100"
    assert totals.fixed_amount_totals == (("CNY", "150"), ("USD", "10"))
    assert totals.residual_installments == 1


@pytest.mark.parametrize(
    ("values", "error"),
    [
        (["1e2"], "calculation_decimal_invalid"),
        (["-1"], "calculation_decimal_invalid"),
        (["1.2.3"], "calculation_decimal_invalid"),
    ],
)
def test_decimal_sum_rejects_non_plain_decimal_values(values: list[str], error: str) -> None:
    with pytest.raises(CalculationInputInvalid, match=error):
        sum_decimal_strings(values)


def test_payment_totals_reject_invalid_or_cross_currency_amounts() -> None:
    with pytest.raises(CalculationInputInvalid, match="fixed_payment_currency_invalid"):
        calculate_payment_totals([{"amount_type": "fixed", "amount": "100", "currency": "CNY/USD"}])
    with pytest.raises(CalculationInputInvalid, match="payment_amount_type_invalid"):
        calculate_payment_totals([{"amount_type": "unknown"}])


def test_price_basis_defaults_to_cny_and_checks_tax_and_amount_forms() -> None:
    result = check_price_basis(
        tax_inclusive_total="1130",
        tax_exclusive_total="1000",
        tax_amount="130",
        fixed_amount="100",
        percentage="10",
        denominator="1200",
    )

    assert result.currency == "CNY"
    assert result.tax_difference == "130"
    assert result.tax_consistent is True
    assert result.percentage_amount == "120"
    assert result.fixed_percentage_consistent is False
    assert result.preferred_amount == "100"


def test_price_basis_marks_missing_comparison_inputs_unknown() -> None:
    result = check_price_basis(
        tax_inclusive_total="1130",
        tax_exclusive_total="1000",
        currency=None,
        percentage="10",
        denominator=None,
    )

    assert result.currency == "CNY"
    assert result.tax_consistent is None
    assert result.percentage_amount is None
    assert result.fixed_percentage_consistent is None


def test_price_basis_percentage_and_tax_arithmetic_do_not_use_decimal_context_precision() -> None:
    amount = "9" * 40
    result = check_price_basis(
        tax_inclusive_total=amount,
        tax_exclusive_total="0",
        tax_amount=amount,
        fixed_amount=amount,
        percentage="100",
        denominator=amount,
    )

    assert result.tax_difference == amount
    assert result.tax_consistent is True
    assert result.percentage_amount == amount
    assert result.fixed_percentage_consistent is True


def test_price_basis_preserves_negative_tax_difference() -> None:
    result = check_price_basis(
        tax_inclusive_total="99.99",
        tax_exclusive_total="100",
        tax_amount="0.01",
    )

    assert result.tax_difference == "-0.01"
    assert result.tax_consistent is False


def test_decimal_sum_rejects_non_sequence_and_oversized_inputs() -> None:
    with pytest.raises(CalculationInputInvalid, match="calculation_values_invalid"):
        sum_decimal_strings("123")  # type: ignore[arg-type]
    with pytest.raises(CalculationInputInvalid, match="calculation_item_limit_exceeded"):
        sum_decimal_strings(["1"] * 501)
