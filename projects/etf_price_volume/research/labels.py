from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow as pa

from .features import array, nullable


def build_labels(
    bars: pa.Table, horizons: list[int], boundary: date
) -> tuple[pa.Table, list[dict]]:
    dates = bars["trade_date"].to_pylist()
    open_, low = array(bars, "research_open"), array(bars, "research_low")
    columns = {"date": bars["trade_date"]}
    exclusions = []
    for h in horizons:
        y, adverse = np.full(len(dates), np.nan), np.full(len(dates), np.nan)
        entry, exit_ = [], []
        for i, d in enumerate(dates):
            valid = i + h + 1 < len(dates) and dates[i + h + 1] <= boundary
            entry.append(dates[i + 1] if valid else None)
            exit_.append(dates[i + h + 1] if valid else None)
            if valid:
                y[i] = open_[i + h + 1] / open_[i + 1] - 1
                adverse[i] = min(0.0, np.min(low[i + 1 : i + h + 1]) / open_[i + 1] - 1)
            else:
                exclusions.append({"date": d, "horizon": h, "reason": "LABEL_END_OUTSIDE_WINDOW"})
        columns[f"Y{h}"] = nullable(y)
        columns[f"MAE{h}"] = nullable(adverse)
        columns[f"entry_date_{h}"] = pa.array(entry, type=pa.date32())
        columns[f"exit_date_{h}"] = pa.array(exit_, type=pa.date32())
    return pa.table(columns), exclusions
