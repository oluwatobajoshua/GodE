from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "data" / "histdata"
for path in sorted(DATA.glob("*_D1.csv")):
    d = pd.read_csv(path, parse_dates=["time"])
    bad = (
        (d["high"] < d["low"])
        | (d["high"] < d["open"])
        | (d["high"] < d["close"])
        | (d["low"] > d["open"])
        | (d["low"] > d["close"])
    ).sum()
    print(
        f"{path.name}: rows={len(d)} {d.time.min().date()}..{d.time.max().date()} "
        f"dupes={d.time.duplicated().sum()} bad_ohlc={bad} nan={int(d.isna().sum().sum())}"
    )
    yearly = d.groupby(d.time.dt.year).size()
    print("  " + " ".join(f"{y}:{n}" for y, n in yearly.items()))
