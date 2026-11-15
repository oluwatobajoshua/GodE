import pandas as pd
from pathlib import Path
base = Path('C:/xampp/htdocs/GodE/research/data/mt5')
for sym in ['EURUSD','GBPUSD','USDJPY']:
    df = pd.read_csv(base / f'{sym}_M1.csv')
    print(f'{sym} M1: bars={len(df)}, spread mean={df["spread"].mean():.1f}, median={df["spread"].median()}, zero_spread={(df["spread"]==0).sum()}')
    print(f'  date range: {df["time"].iloc[0]} -> {df["time"].iloc[-1]}')
