# The 2026-09-16 campaign — how to re-run it

Results and their reading: `docs/IMPROVEMENTS.md` I-62. Every script works on a
COPY of the live database and writes nothing to it.

```sh
W=/tmp/campaign && mkdir -p $W
python3 -c "import sqlite3,os; s=sqlite3.connect('file:'+os.path.expanduser('~/data/investment/investment.db')+'?mode=ro', uri=True); d=sqlite3.connect('$W/copy.db'); s.backup(d)"

R=docs/research/2026-09-16-campaign
uv run python $R/retag.py             $W/copy.db  # every ledger experiment, re-measured with the evidence gate
uv run python $R/campaign.py          $W/copy.db  # cadence variants and the VIX brake prototype
uv run python $R/campaign2.py         $W/copy.db  # simpler-signal variants (books overridden)
uv run python $R/slope_speed_sweep.py $W/copy.db  # SLOPE_SPEED_VETO, the Worker's 2026-09-13 claim
uv run python $R/overlay_cadence.py   $W/copy.db  # the overlay re-read weekly under a monthly book

# Candidate 3 again, on the window the rule never saw (needs I-61's spliced database):
uv run python $R/simpler_oos.py /tmp/pre1993-work/pre1993/pre1993.db
```

`retag.py` and `slope_speed_sweep.py` go through `rule_revision.measure_revision`,
so they record their verdicts in the COPY's ledger; the other two build their
variants outside the registry (a cadence and a VIX rule are not knobs) and only
print. The bootstrap seed is fixed, so a re-run on the same vintage reproduces the
probabilities exactly; a later vintage moves them in the last digits.
