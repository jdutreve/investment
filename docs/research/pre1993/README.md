# The stack out of sample, 1977-1993 — how to reproduce it

The result and its reading live in `docs/IMPROVEMENTS.md` I-61; the question and
the criterion, fixed before any result, in `PREREGISTRATION.md`. This folder is
the means to run it again. Nothing here touches the live database: every script
works on a copy.

```sh
W=/tmp/pre1993-work && mkdir -p $W/pre1993 && cd $W/pre1993

# 1. A copy of the live database (the sqlite backup API is consistent while the agent runs).
python3 -c "import sqlite3,os; s=sqlite3.connect('file:'+os.path.expanduser('~/data/investment/investment.db')+'?mode=ro', uri=True); d=sqlite3.connect('$W/measure.db'); s.backup(d)"

# 2. The sources: FRED and the Ken French data library.
for id in T10Y2Y BAA AAA GS10 DGS10 DBAA DAAA; do
  curl -s -o $id.csv "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$id"; done
for z in F-F_Research_Data_Factors_daily_CSV.zip 6_Portfolios_2x3_daily_CSV.zip; do
  curl -s -O "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/$z" && unzip -o -q $z; done

# 3. From the repository root.
R=docs/research/pre1993
uv run python $R/proxies.py  $W/pre1993 $W/measure.db   # proxies + overlap calibration
uv run python $R/build_db.py $W                          # $W/pre1993/pre1993.db, history spliced in
uv run python $R/test.py     $W/pre1993/pre1993.db       # THE pre-registered test
uv run python $R/eras.py     $W/pre1993/pre1993.db       # 1987 drawdown, rates-rising vs rates-falling split
uv run python $R/verdad.py   $W/pre1993/pre1993.db       # fidelity: Verdad's rule vs the paper's decades
uv run python $R/insample.py $W/measure.db $W/pre1993/pre1993.db  # side finding on 1993-2026
```

The sources are live downloads, so a later run reads later vintages: Fama-French
restates its portfolios as CRSP updates, and the live copy carries whatever the
agent has written since. Expect the numbers to move in the last decimals, not in
the verdict.
