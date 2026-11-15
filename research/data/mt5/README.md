# Broker-native bars (MT5 data plane)

Export with:

```powershell
.\scripts\sync_mt5_data_plane.ps1 -Export
```

Files here override `histdata/` when `canonical_bar_source` is `mt5` in `configs/mt5_data_plane.json`.

Do not commit large CSVs if your workflow re-exports locally; keep `mt5_export_manifest.json` or document export dates in your run log.
