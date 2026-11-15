import pandas as pd
df = pd.read_csv('C:/xampp/htdocs/GodE/research/results/rsi_band_m15m30_sweep.csv')
m1 = df[df['tf']=='M1']
print('M1 all results:')
print(m1[['sym','entry','min_hold','cooldown','aligned','rt','net_design','net_validation','net_oos','net_sharpe','net_dd','all3_pos']].to_string())
print()
print('Best OOS per symbol on M1:')
for sym in ['EURUSD','GBPUSD','USDJPY']:
    sub = m1[m1['sym']==sym]
    best = sub.loc[sub['net_oos'].idxmax()]
    print('  %s: %s mh=%s cd=%s aligned=%s RT=%s OOS=%+.2f%% Sharpe=%.2f DD=%.2f%%' % (
        sym, best['entry'], best['min_hold'], best['cooldown'], best['aligned'],
        best['rt'], best['net_oos'], best['net_sharpe'], best['net_dd']))
