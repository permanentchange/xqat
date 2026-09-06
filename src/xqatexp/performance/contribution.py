from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ContributionResult:
    stock: Decimal
    etf: Decimal
    cash_cost: Decimal

    @property
    def total(self) -> Decimal:
        return self.stock + self.etf + self.cash_cost


def contribution(
    *,
    nav_begin: Decimal,
    nav_end: Decimal,
    stock_begin: Decimal,
    stock_end: Decimal,
    stock_buys: Decimal,
    stock_sells: Decimal,
    stock_dividends: Decimal,
    etf_begin: Decimal,
    etf_end: Decimal,
    etf_buys: Decimal,
    etf_sells: Decimal,
    cash_begin: Decimal,
    cash_end: Decimal,
) -> ContributionResult:
    if nav_begin <= 0:
        raise ValueError("PERFORMANCE_CONTRIBUTION_MISMATCH: invalid beginning NAV")
    stock_pnl = stock_end - stock_begin - stock_buys + stock_sells + stock_dividends
    etf_pnl = etf_end - etf_begin - etf_buys + etf_sells
    cash_pnl = (
        cash_end - cash_begin + stock_buys + etf_buys - stock_sells - etf_sells - stock_dividends
    )
    result = ContributionResult(stock_pnl / nav_begin, etf_pnl / nav_begin, cash_pnl / nav_begin)
    actual = nav_end / nav_begin - 1
    if abs(result.total - actual) > Decimal("1e-10"):
        raise ValueError("PERFORMANCE_CONTRIBUTION_MISMATCH: contributions do not conserve NAV")
    return result
