# Level 2 / tick research data

Populated by `scripts/export_l2_ticks.py` when MetaTrader 5 is running and logged in.

| File | Contents |
|------|----------|
| `{SYMBOL}_l2_ticks.csv` | Tick stream: bid, ask, last, volume, flags (µs time) |
| `{SYMBOL}_l2_book.csv` | Optional live DOM snapshots (top 10 levels) |

The microstructure pipeline uses ticks when present; otherwise M5 synthetic book.
