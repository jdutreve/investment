"""The guide the Worker reads to file a measurable invariant
(src/investment/planner/invariant_vocabulary.py)."""

from investment.db.seed_data import SIGNAL_ALIASES
from investment.mechanical.invariants import Registries, validate_invariant
from investment.planner.invariant_vocabulary import describe_invariant_vocabulary

_REGISTRIES = Registries(
    signals=set(SIGNAL_ALIASES) | {"SPY"},
    asset_classes={"bonds", "cash", "equities"},
    strategies={"market-signal-stack"},
    assets={"SPY"},
    regime_types={"stagflation"},
)
_RANGES = {"inflation": (-1.89, 9.0), "credit_spread": (1.22, 6.16)}


def test_the_guide_names_the_live_signals_with_their_ranges() -> None:
    text = "\n".join(describe_invariant_vocabulary(_REGISTRIES, _RANGES))
    assert "inflation (CPIAUCSL): level -1.89 .. 9.00" in text
    assert "credit_spread (BAA10Y): level 1.22 .. 6.16" in text
    assert "strategies: market-signal-stack" in text
    assert "regime (" not in text  # the regime label is a driver's name, not a driver


def test_every_signal_the_guide_names_passes_the_sweeps_gate() -> None:
    """The guide cannot offer a name the 35y sweep refuses: each listed signal,
    written the way the guide says, validates."""
    lines = describe_invariant_vocabulary(_REGISTRIES, _RANGES)
    named = [line.split()[0] for line in lines if line.startswith("    ") and "(" in line]
    assert set(named) == set(SIGNAL_ALIASES) - {"regime"}
    effect = {
        "handle": "asset-class:bonds",
        "metric": "return",
        "method": "cross_class",
        "direction": "underperform",
    }
    for signal in named:
        condition = [{"signal": signal, "feature": "speed", "op": ">", "value": 0.0}]
        assert validate_invariant(condition, effect, _REGISTRIES) is None, signal
