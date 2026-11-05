import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from services.pipeline.flow import prepare, ingest, FEATURES


def raw_data(n=200):
    return pd.DataFrame({'VendorID': np.ones(n), 'RatecodeID': np.ones(n),
        'PULocationID': np.arange(n) % 200 + 1, 'DOLocationID': np.arange(n) % 150 + 1,
        'passenger_count': np.ones(n), 'tpep_pickup_datetime': pd.date_range('2024-01-01', periods=n, freq='min'),
        'fare_amount': np.arange(n) % 50 + 5.0})


def test_validation_blocks_bad_data():
    with pytest.raises(ValueError, match='Missing required'):
        prepare(raw_data().drop(columns='VendorID'))
    raw = raw_data()
    raw.loc[:100, 'passenger_count'] = -1
    with pytest.raises(ValueError, match='20%'):
        prepare(raw)


def test_preparation_features():
    df = prepare(raw_data())
    assert list(df.columns) == FEATURES + ['total_fare']
    assert len(df) == 200
    assert (df.total_fare > 0).all()


def test_ingestion_retry_does_not_duplicate(tmp_path, monkeypatch):
    path = tmp_path / 'source.parquet'
    raw_data().to_parquet(path)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield path.read_bytes()
    monkeypatch.setattr('services.pipeline.flow.requests.get', lambda *a, **kw: Response())
    db = f'sqlite:///{tmp_path}/db.sqlite'
    monkeypatch.setenv('DATABASE_URL', db)
    ingest.fn(2024, 1, 200)
    ingest.fn(2024, 1, 200)
    with create_engine(db).connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM training_trips')).scalar() == 200
