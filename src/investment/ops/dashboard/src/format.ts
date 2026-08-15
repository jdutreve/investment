/*
 * Formatting, in ONE place — the browser's half of the rule `telegram/digest.py`
 * states for the text fronts: "percent formatting happens here only; every other
 * layer keeps decimal fractions".
 *
 * A MISSING MEASUREMENT IS NEVER A ZERO. `n/a` and `0.00` mean different things
 * and the difference is load-bearing: an unmeasured Sharpe is not a bad one, and
 * a portfolio with no drawdown yet is not one that never falls. Every helper
 * here returns the `n/a` string for null/undefined rather than coercing, which
 * is the same distinction `commands._sharpe` and `digest.pct` make.
 */

export const NA = "n/a";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/*
 * TWO PERCENT FORMATTERS, because this schema has two percent conventions and
 * mixing them is a bug that has already been shipped once. DATA_MODELS pins it:
 * performance indicators (`max_drawdown`, `drawdown`, `volatility`, the returns)
 * are DECIMAL FRACTIONS — `-0.062` = -6.2%; fields suffixed `_pct` or `_rule`,
 * plus `fx_usd_exposure` and `confidence`, are PERCENT POINTS on 0-100.
 *
 * `digest._regime_header` carries the scar: running confidence through the
 * fraction formatter double-converted it, and the live DB's 64.38 rendered as
 * "6438.1%". This page reproduced the same bug on the same field the first time
 * it drew a regime, which is why the distinction now lives in two named
 * functions rather than in whoever is reading the schema that day.
 */

/** A decimal fraction as a percentage: 0.038 -> "+3.8%". */
export function pct(value: unknown, signed = true): string {
  if (!isNum(value)) return NA;
  const s = (value * 100).toFixed(1);
  return `${signed && value > 0 ? "+" : ""}${s}%`;
}

/** A value ALREADY in percent points: 63.31 -> "63.3%". */
export function pctPoints(value: unknown, digits = 1): string {
  return isNum(value) ? `${value.toFixed(digits)}%` : NA;
}

/** An indicator (Sharpe, Sortino, Calmar) to two places. */
export function num(value: unknown, digits = 2): string {
  return isNum(value) ? value.toFixed(digits) : NA;
}

/** A NAV level — no decimals; these run to four figures. */
export function nav(value: unknown): string {
  return isNum(value) ? Math.round(value).toLocaleString("en-US") : NA;
}

/*
 * ALLOCATION MAPS ARE ALREADY 0-100, not a fraction — DATA_MODELS pins it
 * explicitly ("`allocation` MAPs are percent weights summing to 100"), unlike
 * every OTHER weight-like field in this schema, which is a 0-1 fraction
 * (CLAUDE.md: "weight-like fields are 0-1 fractions everywhere" — the one
 * named exception is this one). The first version of this formatter followed
 * the general rule and multiplied by 100, which is correct for nothing this
 * app actually calls it on: every call site passes a `portfolio.allocation`
 * entry or a decision's `held_allocation`/`target_allocation` — both
 * `allocation` MAPs. A live `{"VCIT": 50, "cash": 40, "IWN": 10}` rendered as
 * "5000 / 4000 / 1000" until this was caught against real data.
 */
export function weight(value: unknown): string {
  return isNum(value) ? String(Math.round(value)) : NA;
}

export function date(value: unknown): string {
  return typeof value === "string" && value ? value.slice(0, 10) : NA;
}

/** Sign class for a signed number, or none where there is nothing to sign. */
export function signClass(value: unknown): string {
  if (!isNum(value) || value === 0) return "";
  return value > 0 ? "pos" : "neg";
}

export const isMissing = (v: unknown): boolean => !isNum(v);
