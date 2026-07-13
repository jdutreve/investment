"""Seed constants (docs/TASKS.md Task 1.3, 1ter.1-1ter.6).

Reference-table data plus the static UC0 seed (Framework, RegimeType,
Invariant, Strategy, Scenario, Portfolio) — the graph vertices that do not
depend on market data, regime materialization, or corpus ingestion. This is
what M1 seeds; see docs/MILESTONES.md "Incremental seed" for the steps that
later milestones add.
"""

SYSTEM_THRESHOLDS: dict[str, float] = {
    # ranking + proposal gates
    "rolling_window_days": 756.0,
    "ranking_tiebreak_window": 0.02,
    "proposal_sortino_gap_min": 0.02,
    "proposal_calmar_min": 1.5,
    "proposal_min_allocation_change_pts": 5.0,
    "proposal_max_turnover_pct": 30.0,
    "proposal_expiry_days": 14.0,
    "inbox_quiet_seconds": 300.0,
    "invariant_merge_threshold": 0.80,
    "curation_sanity_ceiling": 40.0,
    "proposal_outcome_weeks": 12.0,
    "proposal_cooldown_weeks": 4.0,
    "proposal_invariant_weight_min": 0.10,
    "invariant_refuted_min_confrontations": 4.0,
    "invariant_refuted_score": 0.35,
    "strategy_probation_weeks": 12.0,
    "scenario_calibration_weeks": 4.0,
    # invariants
    "recency_half_life_days": 365.0,
    "confrontation_margin": 0.10,
    "vector_similarity_min": 0.35,
    # regime detection (see docs/ARCHITECTURE.md formal algorithm)
    "regime_cpi_stagflation": 2.5,
    "regime_cpi_noise": 0.05,
    "regime_cpi_deflation": 0.0,
    "regime_cpi_speed_scale": 0.3,
    "regime_growth_noise": 0.15,
    "regime_growth_speed_scale": 1.0,
    "regime_vix_stress": 25.0,
    "regime_confirm_prints": 2.0,
    # scenarios / misc
    "scenario_shift_trigger": 10.0,
    "min_backtest_periods": 3.0,
    "derivative_lookback_short": 30.0,
    # shadow replay (Phase 9 — go-live gate)
    "replay_cost_bps": 10.0,
    "replay_confirmation_weeks": 2.0,
}

INVARIANT_AUTHOR_CONFIG: list[dict[str, object]] = [
    {"author": "dalio", "floor_weight": 0.40,
     "initial_weight_min": 0.80, "initial_weight_max": 0.90},
    {"author": "marks", "floor_weight": 0.35,
     "initial_weight_min": 0.75, "initial_weight_max": 0.85},
    {"author": "other", "floor_weight": 0.20,
     "initial_weight_min": 0.40, "initial_weight_max": 0.70},
    {"author": "system", "floor_weight": 0.05,
     "initial_weight_min": 0.15, "initial_weight_max": 0.25},
]

ALLOWED_TICKERS: list[dict[str, object]] = [
    {"ticker": "TIP", "asset_class": "US_TIPS", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "TLT", "asset_class": "US_LONG_TREASURY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "IEF", "asset_class": "US_TREASURY_7_10", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "GLD", "asset_class": "GOLD", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "DJP", "asset_class": "COMMODITIES", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "SPY", "asset_class": "US_EQUITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "VTI", "asset_class": "US_EQUITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "QQQ", "asset_class": "US_EQUITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "EFA", "asset_class": "INTL_EQUITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "EEM", "asset_class": "EM_EQUITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "SHY", "asset_class": "US_TREASURY_1_3", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "BIL", "asset_class": "US_TBILL", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "DBC", "asset_class": "COMMODITIES", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^IRX", "asset_class": "RISK_FREE", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^VIX", "asset_class": "VOLATILITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "CHFUSD=X", "asset_class": "FX", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "CPIAUCSL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct", "availability_lag_days": 13},
    {"ticker": "T10Y2Y", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 1},
    {"ticker": "UNRATE", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 7},
    {"ticker": "INDPRO", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct", "availability_lag_days": 16},
    # GLOBAL_LIQUIDITY components (docs/TASKS.md GLOBAL_LIQUIDITY_COMPONENTS) — non-revised
    # in practice (ADR-003 consequences), current-vintage fetch. Lag estimates: WALCL/
    # ECBASSETSW weekly releases (few days); M2SL monthly (~2w, FRED's own calendar);
    # JPNASSETS (BoJ) monthly with a longer lag (~1m).
    {"ticker": "M2SL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 17},
    {"ticker": "WALCL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 5},
    {"ticker": "ECBASSETSW", "asset_class": "MACRO", "currency": "EUR", "source": "fred", "transform": "none", "availability_lag_days": 5},
    {"ticker": "JPNASSETS", "asset_class": "MACRO", "currency": "JPY", "source": "fred", "transform": "none", "availability_lag_days": 30},
    # FX helpers for the GLOBAL_LIQUIDITY USD-conversion (market/liquidity.py usd_convert)
    # — same pattern as CHFUSD=X (display-only elsewhere).
    {"ticker": "EURUSD=X", "asset_class": "FX", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "JPY=X", "asset_class": "FX", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "GROWTH_COMPOSITE", "asset_class": "MACRO", "currency": "USD", "source": "composite", "transform": "composite"},
    {"ticker": "GLOBAL_LIQUIDITY", "asset_class": "GLOBAL_LIQUIDITY", "currency": "USD", "source": "composite", "transform": "composite"},
]
# Macro/composite tickers are exposed to the Worker's market_fetch but are
# NEVER valid allocation assets (Writeback realloc gate 5 checks asset_class).

# The 5 coarse classes benchmark_valuation.asset_class rows use for
# effect.method='cross_class'. Maps fine allowed_tickers.asset_class -> coarse.
BENCHMARK_CLASSES: dict[str, list[str]] = {
    "equities": ["US_EQUITY", "INTL_EQUITY", "EM_EQUITY"],
    "bonds": ["US_LONG_TREASURY", "US_TREASURY_7_10", "US_TREASURY_1_3"],
    "inflation-protected": ["US_TIPS"],
    "gold-commodities": ["GOLD", "COMMODITIES"],
    "cash": ["US_TBILL"],
}
# Excluded (not investable sleeves): MACRO, GLOBAL_LIQUIDITY, VOLATILITY, FX,
# RISK_FREE (^IRX is the risk-free RATE, not an asset).

DERIVED_SIGNALS: dict[str, str] = {
    "GROWTH_COMPOSITE": "INDPRO,UNRATE (see GROWTH_COMPOSITE_COMPONENTS)",
    "GLOBAL_LIQUIDITY": "M2SL,WALCL,ECBASSETSW,JPNASSETS",
    "real_rate": "irx - CPIAUCSL(yoy_pct)   # nominal short rate minus inflation",
}

SIGNAL_ALIASES: dict[str, str] = {
    "inflation": "CPIAUCSL",
    "growth": "GROWTH_COMPOSITE",
    "liquidity": "GLOBAL_LIQUIDITY",
    "irx": "^IRX",
    "real_rate": "real_rate",
    "regime": "regime",
}
# The signal registry = SIGNAL_ALIASES union any raw allowed_tickers series.
# The Writeback VALIDATION GATE rejects a condition signal not in the registry.

# Static reference for USER_BENCHMARK and portfolio.vs_benchmark (replaces
# 60/40 everywhere). Classic Dalio All Weather allocation, regime-agnostic
# by design (a fair yardstick in any regime). Id "all-weather-USD";
# monthly-rebalanced, USD.
#
# Commodity sleeve is DJP, not DBC (verified live at M2 build time): DBC's
# "Optimum Yield" roll strategy diverges persistently from every available
# free commodity-index proxy (correlation ~0.88 with ^BCOM even excluding
# outlier days, checked year by year over its whole history — not a
# fixable data-quality issue) and DBC isn't held by any actual seeded
# portfolio anyway. DJP tracks the plain Bloomberg Commodity Index, splices
# cleanly with ^BCOM back to 1991, and IS what the seeded portfolios hold —
# using it here too means the benchmark's own floor stops being held back
# by an unused ticker.
ALL_WEATHER_BENCHMARK: dict[str, float] = {
    "VTI": 0.30,
    "TLT": 0.40,
    "IEF": 0.15,
    "GLD": 0.075,
    "DJP": 0.075,
}

# Longer-history TOTAL-RETURN series spliced BEFORE each ETF's inception so
# the tradable backtest/benchmark/replay can reach back to ~1991. Used at M2
# (market/fetcher.py + market/splice.py).
#
# Verified at M2 build time (docs/TASKS.md "VERIFY availability + inception"):
# every proxy below round-trips live except the originally-pinned gold/
# commodity sources. GOLDAMGBD228NLBM (FRED's redistribution of the LBMA
# gold fixing) was discontinued (~2021 licensing change) — GLD now sources
# straight from LBMA's own public feed instead (market/fetcher.py
# LBMA_GOLD_AM_URL; free, no key, genuinely daily, verified live back to
# 1968-01-02 against known price levels). SPGSCITR ("index" source) is not
# freely available — commodities use the pinned fallback, ^BCOM (Bloomberg
# Commodity Index, Yahoo, verified live back to 1991), not the 1970 GSCI
# floor originally hoped for.
#
# IEF: VFITX (Vanguard Intermediate-Term TREASURY), not VBMFX (Vanguard
# Total Bond Market — a broad aggregate fund with corporate/MBS exposure,
# a worse conceptual match for IEF's pure 7-10y treasury band) — tested
# both live, VFITX wins (0.945 vs 0.911 correlation) though still needs
# the relaxed MIN_RETURN_CORR (splice.py) to clear the gate.
# SHY: same proxy, but routed through splice.splice_with_resampled_
# validation (see seed.py RESAMPLED_VALIDATION_TICKERS) — SHY's own
# ultra-low volatility makes daily-return correlation noisy regardless of
# proxy choice; validated at monthly resolution instead (0.963).
# BIL: no working proxy found. Tried TB3MS monthly-compounded (0.23),
# TB3MS's daily FRED sibling DTB3 compounded (0.30), VFISX directly
# (0.09), and VFISX at monthly resolution (0.27) — BIL's own distribution/
# NAV-reset pattern doesn't track any of them at any resolution tested.
# Floors at its own ETF inception (2007).
HISTORY_PROXIES: dict[str, tuple[str, str, int]] = {
    "SPY": ("VFINX", "yahoo", 1976),
    "VTI": ("VFINX", "yahoo", 1976),
    "TLT": ("VUSTX", "yahoo", 1986),
    "IEF": ("VFITX", "yahoo", 1986),
    "SHY": ("VFISX", "yahoo", 1991),
    "TIP": ("VIPSX", "yahoo", 2000),
    "GLD": ("LBMA_GOLD_AM", "lbma", 1968),
    "DBC": ("^BCOM", "yahoo", 1991),
    "DJP": ("^BCOM", "yahoo", 1991),
    "BIL": ("TB3MS", "fred", 1934),
}

# ---------------------------------------------------------------------------
# UC0 static seed — Framework, RegimeType, Invariant, Strategy, Scenario,
# Portfolio (docs/TASKS.md Task 1ter.1-1ter.6; docs/USE_CASES.md UC0 steps
# 1-5, 7, 8 — the M1 scope).
# ---------------------------------------------------------------------------

FRAMEWORKS: list[dict[str, object]] = [
    {"id": "4seasons", "name": "Ray Dalio 4 Seasons",
     "description": "Growth x inflation matrix",
     "enabled": True, "accuracy": None,
     "trace": "Primary framework for V1 — see Dalio Principles."},
    {"id": "permanent", "name": "Browne Permanent",
     "enabled": False, "accuracy": None,
     "trace": "Reference framework; not yet active in V1."},
    {"id": "liquidity-cycle", "name": "Global Liquidity Cycle",
     "enabled": False, "accuracy": None,
     "trace": "Reference framework; not yet active in V1."},
]

REGIME_TYPES: list[dict[str, object]] = [
    {"id": "rising-growth-falling-inflation", "name": "Goldilocks",
     "framework_id": "4seasons", "aliases": [],
     "description": "Growth composite rising and CPI YoY decelerating — goldilocks."},
    {"id": "rising-growth-rising-inflation", "name": "Overheating",
     "framework_id": "4seasons", "aliases": ["overheating"],
     "description": "Growth composite rising with CPI YoY accelerating — late cycle."},
    {"id": "falling-growth-rising-inflation", "name": "Stagflation",
     "framework_id": "4seasons", "aliases": ["stagflation"],
     "description": "Growth composite falling with CPI YoY > 2.5 and accelerating."},
    {"id": "falling-growth-falling-inflation", "name": "Disinflation/Recession",
     "framework_id": "4seasons", "aliases": [],
     "description": "Growth composite falling and CPI YoY decelerating; deflation may layer as tag."},
    {"id": "uncertain", "name": "Uncertain",
     "framework_id": "4seasons", "aliases": [],
     "description": "Contradictory or straddled indicators (any flat axis)."},
]

INVARIANTS: list[dict[str, object]] = [
    {"id": "inv-inflation-persistence-tips",
     "title": "Persistent inflation favors TIPS, commodities, and gold",
     "description": "When CPI YoY > 2.5% and speed > 0, real yields fall and "
                    "TIPS/gold/commodities outperform nominal bonds.",
     "example": "2021-2022: TIP +2.3% while TLT -26%.",
     "source": "Dalio — Principles for Navigating Big Debt Crises, ch. inflation",
     "author": "dalio", "status": "proposed",
     "condition": [{"signal": "inflation", "feature": "level", "op": ">", "value": 2.5},
                   {"signal": "inflation", "feature": "speed", "op": ">", "value": 0}],
     "effect": {"handle": "asset-class:inflation-protected", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["tips", "inflation", "gold",
              "asset:TIP", "asset:GLD", "indicator:real-yield",
              "regime:falling-growth-rising-inflation",
              "regime:rising-growth-rising-inflation"],
     "weight_initial": 0.85, "floor_weight": 0.40,
     "trace": "Dalio Principles; chapter on inflation hedges."},
    {"id": "inv-falling-growth-duration",
     "title": "Falling growth favors duration and cash-like defense",
     "description": "Contracting growth with rate-cut expectations supports long "
                    "duration (TLT) and cash equivalents (BIL).",
     "example": "2008 H2, 2019 H2: TLT strongly positive as growth rolled over.",
     "source": "Dalio — Principles for Navigating Big Debt Crises, ch. recession",
     "author": "dalio", "status": "proposed",
     "condition": [{"signal": "growth", "feature": "speed", "op": "<", "value": 0}],
     "effect": {"handle": "asset-class:bonds", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["duration", "recession",
              "asset:TLT", "asset:BIL",
              "regime:falling-growth-falling-inflation"],
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Dalio Principles; recession playbook."},
    {"id": "inv-rising-growth-equities",
     "title": "Rising growth favors equity exposure",
     "description": "Expanding growth with positive earnings revisions supports "
                    "broad equity beta (SPY/VTI).",
     "example": "2016-2018, 2023-2024 expansions.",
     "source": "Standard cycle finance; multi-decade empirical regularity",
     "author": "dalio", "status": "proposed",
     "condition": [{"signal": "growth", "feature": "speed", "op": ">", "value": 0}],
     "effect": {"handle": "asset-class:equities", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["equities", "growth",
              "asset:SPY", "asset:VTI",
              "regime:rising-growth-falling-inflation",
              "regime:rising-growth-rising-inflation"],
     "weight_initial": 0.80, "floor_weight": 0.40,
     "trace": "Standard cycle finance."},
    {"id": "inv-liquidity-tightening-risk",
     "title": "Tightening global liquidity pressures risk assets",
     "description": "GLOBAL_LIQUIDITY level < 100 with speed < 0 historically "
                    "compresses risk-asset multiples.",
     "example": "2018 QT, 2022 tightening.",
     "source": "Howard Marks — memos on cycles and liquidity (multiple, 2008-2023)",
     "author": "marks", "status": "proposed",
     "condition": [{"signal": "liquidity", "feature": "level", "op": "<", "value": 100},
                   {"signal": "liquidity", "feature": "speed", "op": "<", "value": 0}],
     "effect": {"handle": "asset-class:equities", "metric": "return",
                "method": "cross_class", "direction": "underperform"},
     "tags": ["liquidity", "risk", "indicator:global-liquidity"],
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-liquidity-easing-risk",
     "title": "Easing global liquidity supports risk assets",
     "description": "GLOBAL_LIQUIDITY speed > 0 historically expands risk-asset "
                    "multiples.",
     "example": "2020-2021 QE.",
     "source": "Howard Marks — memos on cycles and liquidity (multiple, 2008-2023)",
     "author": "marks", "status": "proposed",
     "condition": [{"signal": "liquidity", "feature": "speed", "op": ">", "value": 0}],
     "effect": {"handle": "asset-class:equities", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["liquidity", "risk", "indicator:global-liquidity"],
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Howard Marks memos on cycles and liquidity."},
    {"id": "inv-diversification-drawdown",
     "title": "Diversification lowers drawdown but dilutes upside",
     "description": "Cross-asset diversification reduces max_drawdown at the "
                    "cost of upside capture in single-regime bull runs.",
     "example": "2008: 60/40 -30% vs All Weather ~-12%.",
     "source": "Dalio — All Weather framework documentation",
     "author": "dalio", "status": "proposed",
     "condition": [],
     "effect": {"handle": "strategy:four-seasons-rp", "metric": "max_drawdown",
                "method": "cross_strategy", "direction": "outperform"},
     "tags": ["diversification", "drawdown",
              "indicator:max_drawdown", "phase:accumulation"],
     "weight_initial": 0.70, "floor_weight": 0.40,
     "trace": "Dalio Principles; All Weather chapter (always-clock; lower "
              "drawdown than the other strategies)."},
]

STRATEGIES: list[dict[str, object]] = [
    {"id": "four-seasons-rp",
     "title": "4 Seasons Dalio Risk Parity",
     "description": "Risk-parity baseline allocating across stocks, long bonds, "
                    "TIPS, gold and commodities to perform in every quadrant.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 65,
     "conditions": "applicable to all regimes; orthogonal: ^VIX level < 30",
     "source": "corpus",
     "trace": "Risk parity baseline."},
    {"id": "permanent-browne",
     "title": "Permanent Portfolio Browne",
     "description": "Browne 25/25/25/25 across stocks, long bonds, gold and cash; "
                    "simplicity baseline with low historical drawdown.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 55,
     "conditions": "regime = uncertain OR regime confidence < 60; "
                   "orthogonal: ^VIX level > 20",
     "source": "corpus",
     "trace": "Simplicity baseline; low historical drawdown."},
    {"id": "barbell-taleb",
     "title": "Barbell Taleb",
     "description": "~85% safety (short/intermediate Treasuries, split across "
                    "SHY/BIL/IEF to respect the 40% single-asset cap) + ~15% "
                    "convexity (equity sleeve) to capture upside while bounding downside.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 45,
     "conditions": "orthogonal: ^VIX level > 25 (tail risk elevated)",
     "source": "corpus",
     "trace": "85% safety + 15% convexity."},
    {"id": "momentum-macro",
     "title": "Momentum Macro",
     "description": "Dynamic rotation by detected regime; tilts toward the "
                    "asset class with strongest current macro momentum.",
     "regime_type_id": None, "framework_id": "4seasons",
     "status": "active", "enabled": True, "conviction": 50,
     "conditions": "regime stable >= 60 days; orthogonal: SPY 90d return > 0",
     "source": "corpus",
     "trace": "Dynamic rotation by detected regime."},
]

BACKED_BY_EDGES: list[tuple[str, str]] = [
    ("four-seasons-rp", "inv-diversification-drawdown"),
    ("four-seasons-rp", "inv-inflation-persistence-tips"),
    ("permanent-browne", "inv-diversification-drawdown"),
    ("barbell-taleb", "inv-falling-growth-duration"),
    ("momentum-macro", "inv-rising-growth-equities"),
    ("momentum-macro", "inv-liquidity-easing-risk"),
]

# 3 per Strategy = 12. four-seasons-rp per docs/TASKS.md Task 1ter.5; the
# other 9 (permanent-browne, barbell-taleb, momentum-macro) were left as
# "... 9 more" in the spec — drafted and owner-approved for M1 (see the
# git history for the approval). permanent-browne keeps a fixed allocation
# across scenarios (Browne's own philosophy: no tactical tilting); barbell-
# taleb tilts modestly (the barbell protects by construction, not by
# timing); momentum-macro rotates fully (its explicit mandate), capped at
# the 40% single-asset user rule.
SCENARIOS: list[dict[str, object]] = [
    # four-seasons-rp
    {"id": "sc-4s-bull", "strategy_id": "four-seasons-rp", "name": "bull",
     "probability": 35,
     "triggers": ["CPI_YOY < 2.5", "GROWTH_COMPOSITE > 102", "Fed dovish"],
     "target_allocation": {"SPY": 35, "TLT": 25, "GLD": 15, "IEF": 15, "DJP": 5, "cash": 5},
     "currency": "USD", "trace": "Goldilocks scenario for 4 Seasons."},
    {"id": "sc-4s-base", "strategy_id": "four-seasons-rp", "name": "base",
     "probability": 45,
     "triggers": ["CPI_YOY 2.5-3.5", "Fed pause"],
     "target_allocation": {"SPY": 30, "TLT": 30, "GLD": 10, "IEF": 20, "DJP": 7.5, "cash": 2.5},
     "currency": "USD", "trace": "Base case for 4 Seasons."},
    {"id": "sc-4s-bear", "strategy_id": "four-seasons-rp", "name": "bear",
     "probability": 20,
     "triggers": ["^VIX > 25", "CPI_YOY > 4 AND GROWTH_COMPOSITE < 98"],
     "target_allocation": {"IEF": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "currency": "USD", "trace": "Stagflation/stress scenario."},
    # permanent-browne — fixed allocation across scenarios, by design
    {"id": "sc-pb-bull", "strategy_id": "permanent-browne", "name": "bull",
     "probability": 30,
     "triggers": ["GROWTH_COMPOSITE > 102", "CPI_YOY < 2.5"],
     "target_allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "currency": "USD", "trace": "Browne fixed allocation — bull macro view."},
    {"id": "sc-pb-base", "strategy_id": "permanent-browne", "name": "base",
     "probability": 45,
     "triggers": ["CPI_YOY 2.5-3.5"],
     "target_allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "currency": "USD", "trace": "Browne fixed allocation — base macro view."},
    {"id": "sc-pb-bear", "strategy_id": "permanent-browne", "name": "bear",
     "probability": 25,
     "triggers": ["^VIX > 25", "GROWTH_COMPOSITE < 98"],
     "target_allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "currency": "USD", "trace": "Browne fixed allocation — bear macro view."},
    # barbell-taleb — modest tilt (the barbell protects by construction)
    {"id": "sc-bt-bull", "strategy_id": "barbell-taleb", "name": "bull",
     "probability": 30,
     "triggers": ["^VIX < 15", "GROWTH_COMPOSITE > 102"],
     "target_allocation": {"SHY": 30, "BIL": 25, "IEF": 20, "SPY": 25},
     "currency": "USD", "trace": "Calm bull — slightly more convex sleeve."},
    {"id": "sc-bt-base", "strategy_id": "barbell-taleb", "name": "base",
     "probability": 45,
     "triggers": ["^VIX 15-25"],
     "target_allocation": {"SHY": 35, "BIL": 30, "IEF": 20, "SPY": 15},
     "currency": "USD", "trace": "Base case — matches seed allocation."},
    {"id": "sc-bt-bear", "strategy_id": "barbell-taleb", "name": "bear",
     "probability": 25,
     "triggers": ["^VIX > 25"],
     "target_allocation": {"SHY": 40, "BIL": 35, "IEF": 20, "SPY": 5},
     "currency": "USD", "trace": "Tail risk — more safety sleeve."},
    # momentum-macro — full tactical rotation, capped at the 40% user rule
    {"id": "sc-mm-bull", "strategy_id": "momentum-macro", "name": "bull",
     "probability": 35,
     "triggers": ["SPY 90d return > 0", "GROWTH_COMPOSITE > 102"],
     "target_allocation": {"SPY": 40, "TLT": 20, "GLD": 15, "DJP": 20, "cash": 5},
     "currency": "USD", "trace": "Strong momentum — equity-tilted, capped at 40%."},
    {"id": "sc-mm-base", "strategy_id": "momentum-macro", "name": "base",
     "probability": 40,
     "triggers": ["regime stable"],
     "target_allocation": {"SPY": 40, "TLT": 30, "GLD": 15, "DJP": 10, "cash": 5},
     "currency": "USD", "trace": "Base case — matches seed allocation."},
    {"id": "sc-mm-bear", "strategy_id": "momentum-macro", "name": "bear",
     "probability": 25,
     "triggers": ["SPY 90d return < 0", "^VIX > 25"],
     "target_allocation": {"SPY": 15, "TLT": 40, "GLD": 20, "DJP": 5, "cash": 20},
     "currency": "USD", "trace": "Negative momentum — bond/gold-tilted, capped at 40%."},
]

PORTFOLIOS: list[dict[str, object]] = [
    {"id": "4s-balanced-defender",
     "name": "4 Seasons Balanced Defender",
     "framework_id": "4seasons", "defender": True, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"IEF": 20, "TLT": 30, "GLD": 10, "DJP": 7.5, "SPY": 30, "cash": 2.5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Initial defender — standard 4 Seasons balanced. TIP swapped "
              "for IEF (docs/MILESTONES.md M2 DoV): TIPS didn't exist "
              "before 1997, so no free proxy can extend TIP's own history "
              "past its 2003 ETF inception (VIPSX/VAIPX/PRTNX/ACITX all "
              "tried, none clear the splice gate cleanly) — IEF is the "
              "closest behavioral match found (corr 0.77 vs TIP, similar "
              "vol/drawdown; gold/commodities correlate at only 0.09-0.26 "
              "and are 3x more volatile) and already reaches 1991."},
    {"id": "4s-stagflation-defensive",
     "name": "4 Seasons Stagflation Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"IEF": 30, "GLD": 25, "DJP": 15, "SPY": 10, "TLT": 10, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 97.5,
     "trace": "Designed for falling-growth-rising-inflation."},
    {"id": "4s-rising-growth-equities",
     "name": "4 Seasons Rising-Growth Equity Tilt",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"SPY": 40, "EFA": 10, "TLT": 15, "GLD": 10, "IEF": 15, "DJP": 5, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 95.0,
     "trace": "Designed for rising-growth quadrants. SPY capped at the "
              "binding 40% user rule; EFA adds intl diversification."},
    {"id": "4s-falling-growth-defensive",
     "name": "4 Seasons Falling-Growth Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"TLT": 40, "IEF": 20, "GLD": 15, "SPY": 15, "cash": 10},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 95.0,
     "trace": "Designed for falling-growth-falling-inflation."},
    {"id": "permanent-balanced",
     "name": "Permanent Portfolio Balanced",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"SPY": 25, "TLT": 25, "GLD": 25, "cash": 25},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 30.0,
     "phase": "accumulation", "fx_usd_exposure": 75.0,
     "trace": "Browne 25/25/25/25; framework-neutral."},
    {"id": "barbell-defensive",
     "name": "Barbell Taleb Defensive",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"SHY": 35, "BIL": 30, "IEF": 20, "SPY": 15},
     "max_drawdown_rule": -10.0,
     "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "85% safety split across SHY/BIL/IEF (binding 40% cap) + 15% convex."},
    {"id": "momentum-macro-rotation",
     "name": "Momentum Macro Rotation",
     "framework_id": "4seasons", "defender": False, "enabled": True,
     "currency": "CHF", "benchmark": "all-weather-USD",
     "allocation": {"SPY": 40, "TLT": 30, "GLD": 15, "DJP": 10, "cash": 5},
     "max_drawdown_rule": -15.0, "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "Dynamic; current allocation reflects last regime."},
]

HOLDS_EDGES: list[tuple[str, str, bool]] = [
    ("4s-balanced-defender", "four-seasons-rp", True),
    ("4s-stagflation-defensive", "four-seasons-rp", True),
    ("4s-rising-growth-equities", "four-seasons-rp", True),
    ("4s-falling-growth-defensive", "four-seasons-rp", True),
    ("permanent-balanced", "permanent-browne", True),
    ("barbell-defensive", "barbell-taleb", True),
    ("momentum-macro-rotation", "momentum-macro", True),
]

DESIGNED_FOR_EDGES: list[tuple[str, str, str]] = [
    ("4s-stagflation-defensive", "falling-growth-rising-inflation", "Designed for stagflation regime."),
    ("4s-rising-growth-equities", "rising-growth-rising-inflation", "Designed for rising-growth quadrants."),
    ("4s-rising-growth-equities", "rising-growth-falling-inflation", "Designed for rising-growth quadrants."),
    ("4s-falling-growth-defensive", "falling-growth-falling-inflation", "Designed for disinflation/recession."),
]
