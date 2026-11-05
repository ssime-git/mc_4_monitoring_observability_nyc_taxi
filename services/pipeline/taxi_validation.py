"""Schema and value validation (pandera). Invalid rows are QUARANTINED with a reason, never dropped silently."""
import numpy as np
import pandas as pd
import pandera as pa
from pandera import Check, Column

SCHEMA = pa.DataFrameSchema({
    'vendor_id': Column(float, Check.in_range(1, 7), nullable=False),
    'rate_code_id': Column(float, Check.in_range(1, 99), nullable=False),
    'pu_location_id': Column(float, Check.in_range(1, 265), nullable=False),
    'do_location_id': Column(float, Check.in_range(1, 265), nullable=False),
    'passenger_count': Column(float, Check.in_range(1, 10), nullable=False),
    'pickup_hour': Column(float, Check.in_range(0, 23), nullable=False),
    'pickup_dayofweek': Column(float, Check.in_range(0, 6), nullable=False),
    'pickup_month': Column(float, Check.in_range(1, 12), nullable=False),
    'pickup_weekday': Column(float, Check.isin([0, 1]), nullable=False),
    'total_fare': Column(float, [Check.gt(0), Check.lt(5000)], nullable=False),
}, coerce=True)


def split_valid_invalid(df: pd.DataFrame):
    """Return (valid_rows, quarantined_rows_with_reason)."""
    df = df.replace([np.inf, -np.inf], np.nan)
    try:
        SCHEMA.validate(df, lazy=True)
        return df, df.iloc[0:0].assign(reason=pd.Series(dtype=str))
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases.dropna(subset=['index'])
        reasons = (cases.assign(reason=cases['column'].astype(str) + ': ' + cases['check'].astype(str))
                   .groupby('index')['reason'].agg('; '.join))
        bad = df.index.isin(reasons.index)
        return df.loc[~bad], df.loc[bad].assign(reason=reasons.reindex(df.index[bad]).values)
