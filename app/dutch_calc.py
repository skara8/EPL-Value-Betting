from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE = 0.05


@dataclass
class DutchSelection:
    label: str
    source: str
    decimal_odds: Optional[float] = None
    polymarket_price: Optional[float] = None  # 0..1
    fee_rate: float = DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE
    maker: bool = False


@dataclass
class DutchResultRow:
    label: str
    source: str
    input_display: str
    effective_odds: float
    stake: float
    gross_return: float
    net_profit: float


@dataclass
class DutchResult:
    total_stake: float
    rows: list[DutchResultRow]
    inverse_sum: float
    combined_decimal_odds: float
    equal_return: float
    equal_profit: float
    return_on_stake_pct: float
    arbitrage: bool
    complete_market: bool


def polymarket_effective_decimal_odds(
    price: float,
    fee_rate: float = DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE,
    maker: bool = False,
) -> float:
    """Convert a Polymarket YES buy price to effective decimal odds.

    Polymarket documents taker fee as:
        fee_usdc = C * feeRate * p * (1-p)

    For a buy, the fee is collected in shares.  A trade for C shares therefore
    delivers net shares C * (1 - feeRate * (1-p)); the cost is C*p.  The
    effective winner payout per dollar staked is consequently:
        (1 - feeRate * (1-p)) / p

    Maker orders use zero fee here because makers are not charged trading fees.
    """
    p = float(price)
    if p <= 0 or p >= 1:
        raise ValueError("Polymarket price must be between 0 and 1.")
    f = 0.0 if maker else max(0.0, float(fee_rate))
    return (1.0 - f * (1.0 - p)) / p


def selection_effective_odds(selection: DutchSelection) -> tuple[float, str]:
    source = (selection.source or "Other").strip()
    if source.lower().startswith("poly"):
        if selection.polymarket_price is None:
            raise ValueError(f"{selection.label}: Polymarket price is missing.")
        odds = polymarket_effective_decimal_odds(
            selection.polymarket_price,
            fee_rate=selection.fee_rate,
            maker=selection.maker,
        )
        display = f"{selection.polymarket_price * 100:.2f}¢"
        return odds, display

    if selection.decimal_odds is None or selection.decimal_odds <= 1:
        raise ValueError(f"{selection.label}: decimal odds must be greater than 1.00.")
    return float(selection.decimal_odds), f"{selection.decimal_odds:.3f}"


def calculate_dutch(
    selections: list[DutchSelection],
    total_stake: float,
    complete_market: bool = True,
) -> DutchResult:
    if total_stake <= 0:
        raise ValueError("Total stake must be greater than zero.")
    if len(selections) < 2:
        raise ValueError("Select at least two outcomes to Dutch.")

    processed: list[tuple[DutchSelection, float, str]] = []
    for selection in selections:
        odds, display = selection_effective_odds(selection)
        processed.append((selection, odds, display))

    inverse_sum = sum(1.0 / odds for _, odds, _ in processed)
    if inverse_sum <= 0:
        raise ValueError("Invalid odds combination.")

    equal_return = total_stake / inverse_sum
    combined = 1.0 / inverse_sum
    equal_profit = equal_return - total_stake
    ros = equal_profit / total_stake * 100.0
    rows: list[DutchResultRow] = []

    for selection, odds, display in processed:
        stake = total_stake * ((1.0 / odds) / inverse_sum)
        gross_return = stake * odds
        rows.append(
            DutchResultRow(
                label=selection.label,
                source=selection.source,
                input_display=display,
                effective_odds=odds,
                stake=stake,
                gross_return=gross_return,
                net_profit=gross_return - total_stake,
            )
        )

    return DutchResult(
        total_stake=total_stake,
        rows=rows,
        inverse_sum=inverse_sum,
        combined_decimal_odds=combined,
        equal_return=equal_return,
        equal_profit=equal_profit,
        return_on_stake_pct=ros,
        arbitrage=complete_market and inverse_sum < 1.0,
        complete_market=complete_market,
    )
