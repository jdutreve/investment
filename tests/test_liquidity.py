"""The GLOBAL_LIQUIDITY composite's vocabulary (market/liquidity.py).

The composite is a STOCK and a MOVEMENT, and until 2026-08-30 three readers of
it each named the state from a different half: the Regime tag demanded both
dimensions agree, the narrative event read the sign of `speed` alone, and
DATA_MODELS defined expansion on the level alone. These tests pin the one
definition that replaced them, and above all its TOTALITY — the property the
old tag rule lacked, and the reason the two transition states existed unnamed.
"""

import pytest

from investment.market import liquidity as L


@pytest.mark.parametrize(
    ("level", "speed", "expected"),
    [
        (105.0, 1.0, L.SUPPORTIVE),
        (105.0, -1.0, L.FADING),
        (95.0, 1.0, L.REPAIRING),
        (95.0, -1.0, L.RESTRICTIVE),
    ],
)
def test_the_four_quadrants(level: float, speed: float, expected: str) -> None:
    """Stock and flow are independent, so there are four states and not two.
    The mixed pair is what the old rule left unnamed."""
    assert L.liquidity_state(level, speed) == expected


@pytest.mark.parametrize(
    ("level", "speed", "expected"),
    [
        # The norm itself is not scarcity: mean-z zero sits on the abundant side.
        (100.0, 1.0, L.SUPPORTIVE),
        (100.0, -1.0, L.FADING),
        # Standing still is not a tailwind — conservative on purpose, and the
        # old narrative did the opposite (`"tightening" if speed < 0 else
        # "easing"` called a flat composite easing).
        (105.0, 0.0, L.FADING),
        (95.0, 0.0, L.RESTRICTIVE),
    ],
)
def test_both_boundaries_land_somewhere(level: float, speed: float, expected: str) -> None:
    """A level of exactly 100 used to receive NO tag from either branch, both
    inequalities being strict; a speed of exactly 0 was narrated as easing."""
    assert L.liquidity_state(level, speed) == expected


def test_every_state_has_a_reading_and_they_are_the_declared_set() -> None:
    """The label map and the state tuple cannot drift apart — a fifth state
    added to one and not the other would render as a KeyError on a Sunday."""
    assert set(L.STATE_READINGS) == set(L.STATES)
    assert len(L.STATES) == 4


def test_a_missing_half_names_no_state() -> None:
    """An early history, or a component that never arrived. Half a reading must
    not be dressed up as a state — every caller treats None as 'do not say'."""
    assert L.liquidity_state(None, 1.0) is None
    assert L.liquidity_state(95.0, None) is None
    assert L.liquidity_state(None, None) is None


def test_the_level_is_restated_as_what_it_measures() -> None:
    """`level = 100 + 10 x mean(z)` is an index on a scale nobody carries in
    their head. 95.84 is not "a bit under 100", it is the components averaging
    0.42 standard deviations under their own five-year norm."""
    assert L.level_in_sigma(95.83616874214097) == pytest.approx(-0.4163831257859)
    assert L.level_in_sigma(100.0) == 0.0
    assert L.level_in_sigma(110.0) == pytest.approx(1.0)


def test_the_components_are_named_once() -> None:
    """`mechanical/catchup.py` and `seed.py` each carried their own copy of this
    tuple before the digest's freshness line would have been the third."""
    from investment.mechanical.catchup import LIQUIDITY_COMPONENTS

    assert LIQUIDITY_COMPONENTS is L.COMPONENTS
    assert set(L.COMPONENTS) == {"M2SL", "WALCL", "ECBASSETSW", "JPNASSETS"}
