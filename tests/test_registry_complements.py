"""Complement tests over the registries that partition a universe.

WHY THIS FILE EXISTS, and why it is not another paragraph in CLAUDE.md. The
most productive review question in this project — "WHEN A SECOND ONE ARRIVES,
FIND WHAT NAMED THE FIRST" — is already written there, and on 2026-08-23 it was
cited in two commit messages by the same author who then broke it four times in
one afternoon. A rule in prose can only be consulted by someone who suspects
they need it, which is exactly the state one is NOT in while confidently reusing
a name. The project's own answer applies to its own rules: encode it, do not
note it.

WHAT THESE TESTS DO THAT AN ORDINARY TEST DOES NOT. A test that asserts what is
IN a set passes silently when the universe grows — the set still contains what
it always did, and the new member is simply unmentioned. A test that asserts the
COMPLEMENT cannot: the new member lands in the difference and reddens until
somebody classifies it. Same information, opposite failure mode, and the failure
mode is the whole point.

TWO KINDS OF REGISTRY, and only one of them is decidable.

  DERIVABLE — the universe is enumerable and membership follows from what the
  thing IS. `NON_PRICE_ASSET_CLASSES` is one: a class either denominates a price
  or it does not. These get a true partition test.

  JUDGED — membership is a decision nobody can derive. Whether a new sleeve
  should be trend-checked, or a new haven exempt from the concentration cap, is
  an investment judgment. These cannot be tested for CORRECTNESS, but the
  omission can still be made loud: pin the difference, and adding to the parent
  forces the decision at the moment it is cheap, instead of surfacing it as a
  frozen stack three years later (`HAVEN_EXEMPT`, 2022 tape, see below).

Adding a registry here is cheap and the coverage compounds; the list of
candidates is in `docs/IMPROVEMENTS.md` under the same heading.
"""

from investment.db.seed_data import ALLOWED_TICKERS, NON_PRICE_ASSET_CLASSES
from investment.mechanical.market_signal import (
    HAVEN_EXEMPT,
    STACK_TICKERS,
    TREND_FALLBACK_HAVEN,
    TREND_HAVEN,
    TREND_SLEEVES,
)

# The other half of `NON_PRICE_ASSET_CLASSES`: classes whose `level` IS a price,
# so percent-of-level momentum is meaningful for them (worker/tools.py).
# Written out rather than derived, because deriving it as "everything else" is
# precisely the assumption that failed — it makes any new class silently a
# price, which is the wrong default for a rate.
PRICE_ASSET_CLASSES = frozenset(
    {
        "COMMODITIES",
        "EM_EQUITY",
        "FX",
        "GOLD",
        "INTL_EQUITY",
        "US_EQUITY",
        "US_IG_CREDIT",
        "US_LONG_TREASURY",
        "US_REAL_ESTATE",
        "US_SMALL_VALUE",
        "US_TIPS",
        "US_TREASURY_1_3",
        "US_TREASURY_7_10",
    }
)


def test_every_asset_class_is_classified_as_price_or_not() -> None:
    """DERIVABLE. `worker/tools._with_normalised_momentum` divides `speed` and
    `acceleration` by `level` for price rows so the Worker can compare momentum
    across tickers, and must not for a rate — dividing a 10bp move in a yield by
    its own level reports "+2.3%", which means nothing.

    The predicate first shipped as `asset_class == "MACRO"`, written from memory
    of a comment. `^IRX` is class RISK_FREE and is a yield; GLOBAL_LIQUIDITY is a
    z-score index. Both would have been normalised. This test is what makes the
    17th class impossible to forget: it fails whichever half the new one belongs
    to, so the choice has to be made rather than defaulted."""
    universe = {str(row["asset_class"]) for row in ALLOWED_TICKERS}
    unclassified = universe - PRICE_ASSET_CLASSES - NON_PRICE_ASSET_CLASSES
    assert not unclassified, (
        f"asset class(es) {sorted(unclassified)} belong to neither half. "
        "Decide whether `level` denominates a PRICE (percent-of-level momentum is "
        "meaningful) or a rate/index (it is not), then add to PRICE_ASSET_CLASSES "
        "here or to NON_PRICE_ASSET_CLASSES in db/seed_data.py."
    )
    assert not (PRICE_ASSET_CLASSES & NON_PRICE_ASSET_CLASSES)


def test_every_stack_sleeve_is_trend_checked_or_is_the_haven() -> None:
    """JUDGED. The overlay is the stack's only downside control, and the sharpest
    line of the 21 M8b Worker readings (2022-02-01, raised in BOTH runs) was that
    it "trends the sleeve it exits but not the sleeve it enters" — it moved 40%
    into IEF while IEF was below its own 200d line, in the worst bond tape of the
    35-year sample. The answer was to check the destination too.

    Nothing can DERIVE that a new sleeve should be trend-checked. What this pins
    is that no sleeve can be silently neither: every member of `STACK_TICKERS` is
    either in the checked set or is the haven, which carries its own check. Add a
    sleeve and this fails until someone says which."""
    uncovered = set(STACK_TICKERS) - set(TREND_SLEEVES) - {TREND_HAVEN}
    assert not uncovered, (
        f"stack sleeve(s) {sorted(uncovered)} are neither trend-checked nor the haven. "
        "The overlay is the stack's only downside control: add them to TREND_SLEEVES, "
        "or say in the code why they are exempt."
    )


def test_the_whole_haven_chain_is_exempt_from_the_concentration_cap() -> None:
    """JUDGED, and the one with a measured cost. The ADR-007 addendum exempted
    IEF from `max_single_asset_pct`, and IEF was the whole haven chain at the
    time. When the haven became trend-checked with a cash fallback (2026-08-07)
    the exemption did not follow, and the flight to safety became unreachable in
    exactly the tape that needs it: on the M8b run of 2026-08-08, four of the
    seven inflation-shock dates produced a 100%-cash target and had it refused by
    the 50% cap. The stack sat in its stale book through the 2022 drawdown.

    `HAVEN_EXEMPT` is derived from the two constants today, so it cannot drift
    from them — this pins the property rather than the derivation, so that a
    third destination added by any route has to join or explain itself."""
    chain = {TREND_HAVEN, TREND_FALLBACK_HAVEN}
    assert chain <= HAVEN_EXEMPT, (
        f"haven destination(s) {sorted(chain - HAVEN_EXEMPT)} are not cap-exempt. "
        "A refusal cannot exit a position, only freeze one, and the target being "
        "refused is the flight to safety itself (ADR-009's reasoning, concentration leg)."
    )
