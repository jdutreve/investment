# skill-detect-us-inflation.md

Rule for classifying the US inflation axis of the 4 Seasons framework.
The inflation axis is NEVER set from a single headline print. It is set from
the **core trend, confirmed across two independent measures (CPI and PCE)**,
read through level, velocity and acceleration.

---

## 1. Primary signal: core, not headline

- The regime input is **core MoM** (ex food & energy), not headline YoY.
- Headline can be dominated by a single volatile component (energy). A rising
  headline with a decelerating core is an **isolated shock**, not a regime.
- Never flip the inflation axis on headline movement alone. Headline is a
  trigger; core is the regime state.

Thresholds (store in `system_thresholds`, do not hardcode):
```
core_mom_disinflation   = 0.20   # <= => disinflation signal
core_mom_plateau_high   = 0.30   # (0.20, 0.30] => sticky plateau
                                 # > 0.30 => reacceleration / loop risk
```

## 2. Two-measure confirmation (CPI × PCE)

CPI and PCE can diverge for weeks (observed June 2026: CPI core 0.0% MoM vs
PCE core 0.3% MoM). A regime flip requires **agreement of both**, not the
faster of the two.

```
if core_cpi_mom <= disinflation AND core_pce_mom <= disinflation:
    inflation_state = "falling"      # confirmed
elif core_cpi_mom > plateau_high AND core_pce_mom > plateau_high:
    inflation_state = "rising"       # confirmed
else:
    inflation_state = "unconfirmed"  # measures disagree -> HOLD, do not rotate
```

- `unconfirmed` never triggers an allocation change. It schedules a re-check at
  the next release of the lagging measure.
- PCE is the Fed's preferred gauge; when only one measure is available, mark the
  state provisional until the other prints.

## 3. Energy pass-through test (self-feeding loop check)

Distinguish an isolated energy shock from a self-feeding loop:

```
energy_shock_isolated =
    headline_yoy rising
    AND core_mom NOT accelerating
    AND core_commodities_mom <= 0     # no goods pass-through
```

- If `energy_shock_isolated` is true, the inflation axis stays anchored on core;
  headline spikes are context, not regime change.
- The loop is only "closing" when energy pass-through reaches core (core_mom
  accelerating over >= 2 consecutive prints). Only then treat as structural.

## 4. Kinematics (level, velocity, acceleration)

For core CPI and core PCE compute:
```
level        = latest core_yoy
velocity     = core_mom (or 3m change of core_yoy)
acceleration = change of velocity over the configured lookback
```
- Acceleration is the early-warning signal for regime transitions.
- A regime transition is *anticipated* (not acted on) when acceleration flips
  sign for 2 consecutive prints. Action still waits for the level/velocity
  confirmation in section 2 (reactive, not predictive — MVP choice).

## 5. Freshness / event override

A print reflects its reference month, which may already be stale.
```
if a known post-reference-month shock exists (e.g. energy re-escalation
   after the reference month):
    downgrade the print's weight; require the next print to confirm before flip.
```
Example: a June CPI showing disinflation is discounted if an energy shock
re-started in early July, after the June reference window.

## 6. Output

Emit to the Regime vertex / MarketEvent:
```
inflation_state : "rising" | "falling" | "unconfirmed"
confidence      : lowered when CPI and PCE disagree, or when a freshness
                  override is active
basis           : "core-cpi+core-pce", with both MoM values and the
                  energy_shock_isolated flag
trace           : MANDATORY — which measures agreed, pass-through result,
                  any freshness override
```

## 7. Action gate (ties to fee discipline)

- `unconfirmed` or single-measure signal => **hold**, no rotation.
- Confirmed flip => rotate, but a partial rotation is allowed on first
  confirmation, completed only after the second measure agrees.
- Rationale: avoids paying transaction costs twice on trigger noise; a wrong
  single-print flip is more expensive than one release of delay.
