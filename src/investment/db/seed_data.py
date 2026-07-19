"""Seed constants (docs/TASKS.md Task 1.3, 1ter.1-1ter.6).

Reference-table data plus the static UC0 seed (Framework, RegimeType,
Invariant, Strategy, Scenario, Portfolio) — the graph vertices that do not
depend on market data, regime materialization, or corpus ingestion. This is
what M1 seeds; see docs/MILESTONES.md "Incremental seed" for the steps that
later milestones add.
"""

SYSTEM_THRESHOLDS: dict[str, float] = {
    # ranking + proposal gates
    "rolling_window_days": 756.0,  # trading-day lookback for ranking indicators
    "ranking_tiebreak_window": 0.02,  # sortino fraction: join current tie group vs open a new one
    "proposal_sortino_gap_min": 0.02,  # switch gate: min sortino_rolling gap over defender (UNWIRED)
    "proposal_calmar_min": 1.5,  # switch gate: absolute floor on challenger calmar_rolling (UNWIRED)
    "proposal_min_allocation_change_pts": 5.0,  # switch/realloc gate: min per-asset change, pts (UNWIRED)
    "proposal_max_turnover_pct": 30.0,  # realloc gate: turnover ceiling, sum(|delta weight|)/2 (UNWIRED)
    # Reallocation delta blend (docs/ARCHITECTURE.md "Proposal/Adaptation
    # delta blending"): delta = 0.4 x scenario_delta + 0.6 x favors_delta.
    # Pinned by the doc, CALIBRATED by Phase 9 (Task 9.2 grids "blend
    # weights"), which is why they are rows here and not constants in code.
    # M6 reads the calibrated favors weight against I-35: the per-regime
    # FAVORS ranking it feeds is noise in 4 of 5 regimes, so a HIGH stable
    # weight on the holdout is suspicious, not confirmation.
    "blend_scenario_weight": 0.4,  # realloc blend: weight on the tactical active-scenario delta
    "blend_favors_weight": 0.6,  # realloc blend: weight on the structural top-FAVORS delta
    "proposal_expiry_days": 14.0,  # pending Proposal -> user_response='expired' after this (UNWIRED)
    "inbox_quiet_seconds": 300.0,  # inbox watcher: quiet time after last drop before a batch (UNWIRED)
    "invariant_merge_threshold": 0.80,  # curation dedup: cosine similarity above which -> merge (UNWIRED)
    "curation_sanity_ceiling": 40.0,  # candidate invariants per document above which -> flagged (UNWIRED)
    "proposal_outcome_weeks": 12.0,  # THE confrontation horizon (backtests, proposal verdicts)
    "proposal_cooldown_weeks": 4.0,  # anti-repetition: weeks after a user rejection before re-cite (UNWIRED)
    "proposal_invariant_weight_min": 0.10,  # realloc gate: min weight_effective to be citable (UNWIRED)
    "invariant_refuted_min_confrontations": 4.0,  # N floor for the REFUTED/INADEQUATE branches
    "invariant_refuted_score": 0.35,  # score below which an amply-confronted invariant is REFUTED
    "strategy_probation_weeks": 12.0,  # weeks a new/revised Strategy runs before auto-keep/close (UNWIRED)
    "scenario_calibration_weeks": 4.0,  # horizon at which a dominant Scenario is scored vs reality (UNWIRED)
    # invariants
    "recency_half_life_days": 365.0,  # days for recency_factor to decay halfway from 1.0 to 0.5
    # Default effect-vs-benchmark no-op band, used for any metric without an
    # explicit per-metric override below.
    "confrontation_margin": 0.10,  # fallback margin for any metric with no per-metric override below
    # Per-metric margins (M5). ONE absolute band cannot serve metrics on
    # incommensurable scales: measured over the real 35y benchmark_valuation,
    # four-seasons-rp's max_drawdown differs from the median of the other
    # strategies by at most +-0.041, so the 0.10 default swallowed 100% of
    # 1812 moments (0 confirmations AND 0 infirmations) and made
    # inv-diversification-drawdown permanently unmaturable. Bands are sized
    # to each metric's observed dispersion; Phase 9 calibrates them like every
    # other threshold.
    # Sized to the CONFRONTATION HORIZON (proposal_outcome_weeks = 12w), not
    # to the 756d ranking window: 2pts over a quarter is ~8.7%/y annualized —
    # an economically meaningful edge — and it sits below the median
    # cross-class dispersion of |handle - benchmark| measured on the real 35y
    # data (0.015 inflation-protected .. 0.049 equities), so moments actually
    # resolve. The inherited 0.10 was a 3y-window value: over 12w it swallowed
    # 99.4% of inflation-protected moments and left
    # inv-inflation-persistence-tips with 0 confrontations, unmaturable.
    "confrontation_margin_return": 0.02,  # no-op band (fraction) for the 'return' metric
    "confrontation_margin_max_drawdown": 0.01,  # no-op band (fraction) for the 'max_drawdown' metric
    "confrontation_margin_sortino_rolling": 0.15,  # no-op band for the 'sortino_rolling' metric
    "confrontation_margin_volatility": 0.02,  # no-op band (fraction) for the 'volatility' metric
    "vector_similarity_min": 0.35,  # curation: min embedding cosine sim to create a SUPPORTS edge (UNWIRED)
    # time-validation verdict gate (ARCHITECTURE.md "Birth maturation"):
    # confrontations >= N_min AND market_score >= theta AND the Wilson lower
    # bound clears the null AND not refuted.
    # Documented in DATA_MODELS.md system_thresholds description but missing
    # from this seed until M5 — filled in here.
    "invariant_min_confrontations": 3.0,  # N_min: confrontations needed before INTEGRATED can fire
    "invariant_time_validation_score": 0.60,  # theta: score an invariant must clear for INTEGRATED
    # Verdict convergence (ADR-006 amendment, M5): one-sided confidence for
    # BOTH bounds — 'inadequate' rejection when the Wilson upper bound of
    # market_score is < theta (demonstrably cannot reach the bar), and
    # integration only when the Wilson lower bound clears the null below.
    # The upper bound is what empties the 0.35-0.60 dead middle ("Nothing
    # stays proposed forever"); 'proposed' means insufficient evidence only.
    "invariant_verdict_confidence": 0.95,
    # The no-condition null of a BASELINE-RELATIVE market_score (a
    # confirmation means "beat what this handle does anyway", so a
    # zero-skill invariant scores 0.50 — see invariants.py `baseline_excess`).
    # Integration requires the Wilson LOWER bound to clear it (ADR-006
    # amendment, M5-bis): theta alone is a point test that gets EASIER the
    # less evidence there is — at N_min=3 a zero-edge invariant integrated
    # on a coin flip, which is how TIPS held 'integrated' on 9/14.
    "invariant_null_score": 0.50,
    # regime detection (see docs/ARCHITECTURE.md formal algorithm)
    # Calibrated at M3 by a grid search over the REAL 35y history (the
    # pre-M2 hand-guessed values produced 23% whipsaws and an "Overheating"
    # call spanning the Sep-Oct 2008 Lehman collapse): every
    # (noise x confirm_prints x smoothing) combo was replayed over 1991-2026
    # and scored against 7 historical episodes the detector MUST register
    # (1994 rate shock, 1997-98 Asia/LTCM, 2000-01 dot-com recession, the
    # 2008 stagflation leg, the 2009 deflation leg, the 2020 COVID crash,
    # the 2021-22 stagflation), maximizing hits then minimizing whipsaws.
    # Winner: 7/7 events, 90 episodes, 4% whipsaw rate, no blackout > 14mo.
    # The design point that wins is LOW noise thresholds (sensitivity
    # preserved) with the chop suppressed by SMOOTHING + a 3-print
    # confirmation — not a wide noise band, which just goes blind for years
    # (a 0.9 growth-noise variant spent 1991-2001 in one "uncertain").
    # speed_scale = p90 of the SMOOTHED |speed| distribution (confidence
    # normalization only, no effect on detection).
    "regime_cpi_stagflation": 2.5,  # CPI YoY (pct) above which rising speed reads 'rising' unconditionally
    "regime_cpi_noise": 0.04,  # dead-band on smoothed CPI YoY speed below which direction reads 'flat'
    "regime_cpi_deflation": 0.0,  # CPI YoY (pct) below which the 'deflation' tag is applied to a Regime
    "regime_cpi_speed_scale": 0.4,  # normalization scale (p90 smoothed CPI speed) for inflation confidence
    "regime_growth_noise": 0.3,  # dead-band on smoothed GROWTH_COMPOSITE speed below which direction reads 'flat'
    "regime_growth_speed_scale": 3.0,  # normalization scale (p90 smoothed growth speed) for growth confidence
    "regime_vix_stress": 25.0,  # ^VIX level above which the 'stress' tag is applied to the detected Regime
    "regime_confirm_prints": 3.0,  # consecutive monthly prints required (hysteresis) to commit/flip a Regime
    # The detector classifies DIRECTION from a trailing moving average of
    # speed (the persisted market_data level/speed/acceleration stay exactly
    # as TASKS.md Task 2.2 pins them — only the detector's own read is
    # smoothed): a bare 1-month diff of the z-amplified composite is
    # dominated by single-month noise — a lone +6.3 bounce inside the 2008
    # collapse reads as "rising growth" at ANY noise threshold otherwise.
    "regime_speed_smoothing_months": 4.0,
    # scenarios / misc
    "scenario_shift_trigger": 10.0,  # dominant-scenario prob-pt shift that triggers an off-cycle UC8 (UNWIRED)
    "min_backtest_periods": 3.0,  # min completed Regime instances before a RegimeType gets Backtest/FAVORS rows
    "derivative_lookback_short": 30.0,  # days: growth/inflation derivative lookback + benchmark_valuation lookback
    # shadow replay (Phase 9 — go-live gate)
    "replay_cost_bps": 10.0,  # per-side trading cost applied to turnover in the replay cost model (UNWIRED)
    "replay_confirmation_weeks": 2.0,  # replay harness acceptance-policy confirmation window (UNWIRED)
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
    {"ticker": "DBC", "asset_class": "COMMODITIES", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^IRX", "asset_class": "RISK_FREE", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "^VIX", "asset_class": "VOLATILITY", "currency": "USD", "source": "yahoo", "transform": "none"},
    {"ticker": "CHFUSD=X", "asset_class": "FX", "currency": "USD", "source": "yahoo", "transform": "none"},
    # Revised macro series (ADR-003): fetched as ALFRED first-release vintages
    # (market/fetcher.py REVISED_SERIES), so each observation is dated at its
    # true vintage publication date — NO availability_lag_days applies (it is a
    # fallback only for the current-vintage path, which these never take).
    {"ticker": "CPIAUCSL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct"},
    {"ticker": "UNRATE", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none"},
    {"ticker": "INDPRO", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "yoy_pct"},
    # Non-revised (current-vintage fetch): dated at reference date + availability_lag_days.
    {"ticker": "T10Y2Y", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 1},
    # 10-year constant-maturity yield — the LEVEL, which T10Y2Y (a 10y-2y
    # spread) does not carry and ^IRX (13-week bill) is the wrong maturity
    # for. Fetched for the `real_yield_10y` derived signal below: the LONG
    # real yield is what gold's opportunity-cost claim is stated against, and
    # it partitions history very differently from the short real rate
    # (irx - CPI YoY sits below 2.5% for 88% of 1991-2026).
    {"ticker": "DGS10", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 1},
    # GLOBAL_LIQUIDITY components (market/liquidity.py) — non-revised
    # in practice (ADR-003 consequences), current-vintage fetch. Lag estimates: WALCL/
    # ECBASSETSW weekly releases (few days); M2SL monthly (~2w, FRED's own calendar);
    # JPNASSETS (BoJ) monthly with a longer lag (~1m).
    {"ticker": "M2SL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 17},
    {"ticker": "WALCL", "asset_class": "MACRO", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 5},
    {"ticker": "ECBASSETSW", "asset_class": "MACRO", "currency": "EUR", "source": "fred", "transform": "none", "availability_lag_days": 5},
    {"ticker": "JPNASSETS", "asset_class": "MACRO", "currency": "JPY", "source": "fred", "transform": "none", "availability_lag_days": 30},
    # FX helpers for the GLOBAL_LIQUIDITY USD-conversion (market/liquidity.py
    # usd_convert). FRED, not Yahoo: DEXUSEU (USD per EUR, from 1999) and DEXJPUS
    # (JPY per USD, from 1971) reach back far enough that WALCL (2002) — not the
    # FX feed — sets the composite's floor; Yahoo's EURUSD=X/JPY=X only start
    # ~2003 and would have gated the composite two years late (skipna=False).
    {"ticker": "DEXUSEU", "asset_class": "FX", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 1},
    {"ticker": "DEXJPUS", "asset_class": "FX", "currency": "USD", "source": "fred", "transform": "none", "availability_lag_days": 1},
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
# The US_TBILL sleeve carries no fetchable ticker: it is represented by the
# synthetic 'cash' asset, which accrues at rf_daily from ^IRX (docs/TASKS.md
# NAV conventions; reallocation gate allows "... or 'cash'") and floors at
# 1960. BIL (the former US_TBILL ETF) was retired — same economic role, worse
# history (2007), and no viable HISTORY_PROXIES splice at any resolution
# tested. The cash cross_class benchmark (M5) reads that synthetic series.
# Excluded (not investable sleeves): MACRO, GLOBAL_LIQUIDITY, VOLATILITY, FX,
# RISK_FREE (^IRX is the risk-free RATE, not an asset).

DERIVED_SIGNALS: dict[str, str] = {
    "GROWTH_COMPOSITE": "INDPRO,UNRATE (see market/growth.py)",
    "GLOBAL_LIQUIDITY": "M2SL,WALCL,ECBASSETSW,JPNASSETS",
    "real_rate": "irx - CPIAUCSL(yoy_pct)   # nominal SHORT rate minus inflation",
    # The LONG real yield — a distinct signal, not a refinement of real_rate:
    # opportunity-cost claims (gold vs a yielding alternative) are stated
    # against the 10y, and the two disagree on most of the sample (the short
    # real rate is < 2.5% for 88% of 1991-2026; the long one is not).
    "real_yield_10y": "DGS10 - CPIAUCSL(yoy_pct)  # nominal 10y yield minus inflation",
    # BROAD-MONEY family. Distinct from GLOBAL_LIQUIDITY, which is a 5y
    # z-score of CENTRAL-BANK balance sheets + M2: base money and broad money
    # diverge hard (QE inflates CB assets without proportional deposit
    # growth), GLOBAL_LIQUIDITY floors at 2003 on WALCL, and its speed/
    # acceleration are 7-DAY (a wiggle) where a money-growth claim is annual.
    # M2 is the ONLY live broad aggregate: every US M3 series is discontinued
    # (OECD MABMM301USM189S ends 2023-11; the Fed's own M3 died in 2006), so
    # an M3-conditioned invariant cannot be evaluated forward at all.
    "m2_yoy": "M2SL year-over-year %  # broad money GROWTH, monthly, from 1959",
    "m2_accel_12m": "m2_yoy - m2_yoy(12m ago)  # is money growth FASTER than a year ago",
    # Time-series momentum / tactical-allocation trend filter (Faber;
    # Moskowitz-Ooi-Pedersen): >0 iff price sits above its 10-month SMA.
    # Confirms that liquidity is actually transmitting into risk-asset prices.
    "equity_trend": "SPY / SMA10m(SPY) - 1  # equity price vs its 10-month average",
}

SIGNAL_ALIASES: dict[str, str] = {
    "inflation": "CPIAUCSL",
    "growth": "GROWTH_COMPOSITE",
    "liquidity": "GLOBAL_LIQUIDITY",
    "irx": "^IRX",
    "real_rate": "real_rate",
    "real_yield": "real_yield_10y",
    # 'liquidity' is CENTRAL-BANK liquidity (GLOBAL_LIQUIDITY); 'broad_money'
    # is DEPOSIT money (M2). Not variants — see ARCHITECTURE CURATOR RULE 2.
    "broad_money": "m2_yoy",
    "broad_money_accel": "m2_accel_12m",
    "equity_trend": "equity_trend",
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
# (BIL, the former US_TBILL ETF, was retired rather than spliced: no working
# proxy at any resolution tested — TB3MS monthly/daily-compounded 0.23/0.30,
# VFISX direct/monthly 0.09/0.27 — and the synthetic 'cash' asset already
# covers the same sleeve with a 1960 floor. See BENCHMARK_CLASSES above.)
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
    # FDIVX (Fidelity Diversified International, since 1991-12-27) — clears
    # the STANDARD 0.95 correlation bar (0.954), no exception needed. Beat
    # SCINX/PRITX/VWIGX/AEPGX (all older but 0.91-0.94, would need the
    # named 0.94 exception) and VGTSX (cleaner at 0.961 but only 1996).
    "EFA": ("FDIVX", "yahoo", 1991),
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
                    "duration (TLT) and the cash sleeve.",
     "example": "2008 H2, 2019 H2: TLT strongly positive as growth rolled over.",
     "source": "Dalio — Principles for Navigating Big Debt Crises, ch. recession",
     "author": "dalio", "status": "proposed",
     "condition": [{"signal": "growth", "feature": "speed", "op": "<", "value": 0}],
     "effect": {"handle": "asset-class:bonds", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["duration", "recession",
              "asset:TLT", "asset:cash",
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
    # Owner-supplied revision (2026-07-15) after independent 1991-2025
    # validation. Conformed on entry; every departure is mechanical:
    #   signal 'liquidity'      -> 'broad_money' + 'broad_money_accel'. The
    #     trace specifies OECD M3 YoY with a 12-MONTH acceleration; our
    #     'liquidity' is a 5y z-score of CB balance sheets + M2 whose
    #     speed/acceleration are 7-DAY. Measured: the two readings of the
    #     submitted condition disagree on 42.7% of days, and GLOBAL_LIQUIDITY
    #     floors at 2003 (WALCL), halving the cited window. M3 itself is
    #     UNUSABLE — every US series is discontinued (OECD MABMM301USM189S
    #     ends 2023-11, the Fed's own M3 died 2006), so an M3 condition could
    #     never be evaluated forward. Owner chose M2 (live, 1959+) as the
    #     broad-money aggregate: same SHAPE (growth positive + accelerating
    #     year-over-year), different aggregate, so the 9/10 evidence below
    #     does NOT transfer and the engine's sweep is an INDEPENDENT test.
    #   signal 'equity-trend'   -> 'equity_trend' (registry key; the new
    #     SPY/SMA10m-1 derived signal, added for this invariant)
    #   metric 'relative_return'-> 'return' (the computed indicator;
    #     cross_class is ALREADY what makes it relative — third submission
    #     carrying this; the gate now demotes it rather than crashing)
    #   weight_initial 0.70     -> 0.75 ('marks' tier floor_min; CLAUDE.md
    #   floor_weight   0.40     -> 0.35  pins marks = 0.35, 0.40 is dalio)
    # STRIPPED (ADR-006 — belief does not grant integration):
    #   status 'integrated' -> 'proposed'; validated_at -> null;
    #   market_score 0.90 / counts 9-1 / weight_effective 0.63 -> birth
    #   defaults. The submitted evidence stays in `source`/`trace`.
    {"id": "inv-liquidity-easing-risk",
     "title": "Accelerating broad money confirmed by positive equity trend favors equities",
     "description": "When broad money growth is positive and accelerating year-over-year, "
                    "and the equity market remains above its long-term trend, equities tend "
                    "to outperform cash and usually nominal government bonds. The "
                    "market-trend condition confirms that monetary liquidity is effectively "
                    "transmitting into risk-asset prices.",
     "example": "1991-2025 owner validation (on OECD M3, not the M2 this invariant now "
                "reads): 10 independent annual signals; the following year's S&P 500 total "
                "return beat 3-month bills in 10/10 and beat both bills and 10y Treasuries "
                "in 9/10. Mean 19.4%, worst +5.5%. The UNCONFIRMED liquidity signal gave "
                "13/16 and included a -36.6% outcome — the trend filter is what removes it.",
     "source": "Howard Marks — memos on cycles and liquidity; OECD/FRED broad money; "
               "Damodaran historical returns; Moskowitz, Ooi and Pedersen — Time Series "
               "Momentum; Faber — A Quantitative Approach to Tactical Asset Allocation",
     "author": "marks", "status": "proposed",
     "condition": [{"signal": "broad_money", "feature": "level", "op": ">", "value": 0},
                   {"signal": "broad_money_accel", "feature": "level", "op": ">", "value": 0},
                   {"signal": "equity_trend", "feature": "level", "op": ">", "value": 0}],
     "effect": {"handle": "asset-class:equities", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["liquidity", "risk", "equities", "trend", "momentum",
              "asset:SPY", "indicator:broad-money", "indicator:equity-trend",
              "comparator:cash", "comparator:nominal-bonds",
              "trend:sma10", "horizon:12m", "validation:1991-2025"],
     "weight_initial": 0.75, "floor_weight": 0.35,
     "trace": "Owner validation on 416 monthly observations 1991-2025 using OECD M3 YoY "
              "(October reading, 2-month publication lag) and SPY/SMA10m: 10/10 vs bills, "
              "9/10 vs bills AND 10y (market_score 0.90), mean next-year return 19.4%, "
              "worst +5.5%, present in every decade. Fisher exact one-sided p ~0.064 vs "
              "cash, ~0.077 vs both; strong but not conclusive at N=10, and OECD values are "
              "current-vintage (later revisions possible). Credit spreads were tested and "
              "dropped: their first derivative shrank the sample and added instability. "
              "CAVEATS on the engine's own re-test: (a) it reads M2, not M3 — every US M3 "
              "series is discontinued, so the cited evidence does not transfer and this is "
              "an independent test; (b) the engine confronts at proposal_outcome_weeks "
              "(12 WEEKS), not the 12-MONTH horizon validated here (docs/IMPROVEMENTS.md "
              "I-32)."},
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
    # Owner-supplied, revised 2026-07-15 after independent 1991-2026 validation.
    # Conformed to the schema/registry on entry; every departure is mechanical:
    #   signal 'real-yield'        -> 'real_yield' (registry key -> the NEW
    #                                 real_yield_10y derived signal, added for
    #                                 this invariant: the trace pins "10-year
    #                                 nominal minus CPI YoY", which no existing
    #                                 signal carried. NOT 'real_rate' — that is
    #                                 the 13-week bill and partitions history
    #                                 differently)
    #   metric 'relative_return'   -> 'return'     (the computed indicator;
    #                                 method=cross_class is ALREADY what makes
    #                                 it relative)
    #   author 'world-gold-council'-> null         ('other corpus' tier —
    #                                 CLAUDE.md pins 4 tiers; WGC is not one)
    #   weight_initial 0.80        -> 0.70         ('other' tier ceiling;
    #   floor_weight   0.40        -> 0.20          0.80/0.40 is the DALIO tier)
    # STRIPPED (ADR-006: belief does not grant integration, history does — a
    # supplied verdict is precisely what the engine exists to withhold):
    #   status 'integrated'  -> 'proposed'  (birth status; the engine rules)
    #   validated_at         -> null        (set mechanically iff integrated)
    #   market_score 0.78    -> 1.0         (pre-confrontation default; note
    #                                        0.78 also disagrees with the
    #                                        supplied 4/(4+2)=0.667 counts)
    #   confirmation/infirmation_count 4/2 -> 0/0  (the engine's own sweep)
    #   weight_effective 0.624 -> derived from the engine's market_score
    # The submitted evidence is preserved in `source`/`trace` as provenance.
    # handle stays asset:GLD (NOT asset-class:gold-commodities): the claim is
    # about GOLD, and that class blends GLD with DJP/DBC.
    {"id": "inv-low-real-yields-favor-gold",
     "title": "Low real yields favor gold versus cash and nominal bonds",
     "description": "When the US 10-year real yield is below 2.5%, gold's expected "
                    "absolute return improves materially relative to high-real-yield "
                    "regimes, and gold tends to outperform cash and nominal Treasury "
                    "bonds. Relative outperformance versus equities is weaker and not "
                    "systematic. Negative real yields do not provide a stronger signal "
                    "than real yields between 0% and 2.5%.",
     "example": "1991-2026: when the 10-year real-yield proxy was below 2.5%, gold "
                "returned 9.8% annualized versus 5.9% for the S&P 500 price index, "
                "4.1% for a rolling 10-year Treasury proxy, and 1.9% for 3-month "
                "bills. Above 2.5%, gold returned 3.1% versus 15.0%, 6.7%, and 4.0%, "
                "respectively. Average forward 12-month gold return was 12.0% below "
                "2.5% versus 2.3% above.",
     "source": "Independent market validation, 1991-08 to 2026-04; World Bank Pink "
               "Sheet gold prices; Federal Reserve H.15 Treasury yields; BLS CPI; "
               "Shiller/FRED S&P 500 data",
     "author": None, "status": "proposed",
     "condition": [{"signal": "real_yield", "feature": "level", "op": "<", "value": 2.5}],
     "effect": {"handle": "asset:GLD", "metric": "return",
                "method": "cross_class", "direction": "outperform"},
     "tags": ["gold", "real-yield", "interest-rates",
              "asset:GLD", "indicator:real-yield", "comparator:cash",
              "comparator:nominal-bonds", "comparator:equities",
              "regime:low-real-rates", "validation:1991-2026"],
     "weight_initial": 0.70, "floor_weight": 0.20,
     "trace": "Owner-supplied backtest on 416 monthly observations 1991-08..2026-04. "
              "Real-yield proxy = US 10-year nominal Treasury yield minus prior-month "
              "CPI YoY. A real yield below 2.5% increased average forward 12-month "
              "gold return by 9.7pp vs the high-yield regime (HAC p=0.021). Regime "
              "differential: +14.4pp vs S&P 500 price (p=0.031), +14.8pp vs rolling "
              "10y Treasuries (p=0.001), +11.9pp vs 3-month bills (p=0.005). Negative "
              "real yields did NOT beat the 0-2.5% regime (4.2% vs 15.2%): the "
              "'especially negative' clause and universal equity outperformance were "
              "both rejected. NOTE the horizon mismatch: that evidence is forward "
              "12-MONTH, this engine confronts at proposal_outcome_weeks (12 WEEKS) — "
              "see docs/IMPROVEMENTS.md I-32."},
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
     "description": "~85% safety (short/intermediate Treasuries plus cash, split "
                    "across SHY/cash/IEF to respect the 40% single-asset cap) + ~15% "
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
    # A Scenario's trigger LIST is a DISJUNCTION; one STRING ANDs its own
    # predicates (mechanical/scenarios.py `evaluate_trigger_series`, matching
    # docs/ARCHITECTURE.md "Strategy '4 Seasons' — example"). So a bull case —
    # a conjunction of good things CO-OCCURRING ("goldilocks" IS low inflation
    # AND high growth) — is ONE string; a bear case — alternative routes to
    # the same damage (panic OR stagflation) — is several. M5 seeded the bulls
    # as separate items while the code ANDed the whole list, which read the
    # same either way; under the corrected OR they would have become "low
    # inflation OR high growth", so they are merged here.
    # four-seasons-rp
    {"id": "sc-4s-bull", "strategy_id": "four-seasons-rp", "name": "bull",
     "probability": 35,
     "triggers": ["CPI_YOY < 2.5 AND GROWTH_COMPOSITE > 102", "Fed dovish"],
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
     "triggers": ["GROWTH_COMPOSITE > 102 AND CPI_YOY < 2.5"],
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
     "triggers": ["^VIX < 15 AND GROWTH_COMPOSITE > 102"],
     "target_allocation": {"SHY": 30, "cash": 25, "IEF": 20, "SPY": 25},
     "currency": "USD", "trace": "Calm bull — slightly more convex sleeve."},
    {"id": "sc-bt-base", "strategy_id": "barbell-taleb", "name": "base",
     "probability": 45,
     "triggers": ["^VIX 15-25"],
     "target_allocation": {"SHY": 35, "cash": 30, "IEF": 20, "SPY": 15},
     "currency": "USD", "trace": "Base case — matches seed allocation."},
    {"id": "sc-bt-bear", "strategy_id": "barbell-taleb", "name": "bear",
     "probability": 25,
     "triggers": ["^VIX > 25"],
     "target_allocation": {"SHY": 40, "cash": 35, "IEF": 20, "SPY": 5},
     "currency": "USD", "trace": "Tail risk — more safety sleeve."},
    # momentum-macro — full tactical rotation, capped at the 40% user rule.
    # Its bull/bear are NOT merged into one AND-string like the others: "SPY
    # 90d return" is outside the numeric grammar (I-22), and a string is
    # unparseable as a WHOLE if any conjunct is — merging would leave bull
    # with no parseable disjunct at all, and with 'base' already qualitative
    # that is two triggerless scenarios, which defeats the single-residual
    # rule and hands bear 100%. Left as a disjunction whose SPY branch simply
    # drops out: the rate reads on GROWTH_COMPOSITE alone, as it did at M5.
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
     "allocation": {"SHY": 35, "cash": 30, "IEF": 20, "SPY": 15},
     "max_drawdown_rule": -10.0,
     "max_single_asset_pct": 40.0,
     "phase": "accumulation", "fx_usd_exposure": 100.0,
     "trace": "85% safety split across SHY/cash/IEF (binding 40% cap) + 15% "
              "convex. BIL swapped for 'cash' (docs/MILESTONES.md M2 DoV): "
              "'cash' accrues at rf_daily from ^IRX directly (no ETF fetch, "
              "no splice needed, floors at 1960) — the exact same economic "
              "role BIL played, and BIL itself has no viable proxy at any "
              "resolution tested (TB3MS monthly/daily-compounded, VFISX "
              "direct/monthly: 0.09-0.30 correlation, all rejected)."},
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
