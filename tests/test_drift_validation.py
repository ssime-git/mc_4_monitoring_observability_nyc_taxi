import numpy as np
import pandas as pd
import pytest
import yaml

from services.pipeline import flow, taxi_drift, taxi_validation
from tests.test_pipeline import raw_data

FEATURES = flow.FEATURES


def sample(n=3000, seed=0, shift=0.0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        'vendor_id': rng.choice([1, 2], n), 'rate_code_id': rng.choice([1, 2], n, p=[.9, .1]),
        'pu_location_id': np.clip(rng.normal(130 + shift, 40, n), 1, 265).round(),
        'do_location_id': np.clip(rng.normal(140, 50, n), 1, 265).round(),
        'passenger_count': rng.choice([1, 2, 3], n, p=[.7, .2, .1]),
        'pickup_hour': rng.integers(0, 24, n), 'pickup_dayofweek': rng.integers(0, 7, n),
        'pickup_month': np.ones(n), 'pickup_weekday': rng.integers(0, 2, n)})


def test_no_drift_between_two_samples_of_the_same_distribution():
    result = taxi_drift.feature_drift(sample(seed=1), sample(seed=2), FEATURES)
    assert result['drift_detected'] is False
    assert result['drift_share'] == 0.0
    assert all(f['psi'] < 0.1 for f in result['features'].values())


def test_injected_drift_is_detected_on_the_right_feature():
    current = sample(seed=2, shift=70)  # pickup zones move by +70
    result = taxi_drift.feature_drift(sample(seed=1), current, FEATURES)
    assert result['features']['pu_location_id']['drifted'] is True
    assert result['features']['do_location_id']['drifted'] is False
    assert result['features']['pu_location_id']['psi'] > 0.2


def test_dataset_drift_requires_enough_drifted_features():
    current = sample(seed=2)
    for col in ['pu_location_id', 'do_location_id', 'pickup_hour', 'passenger_count']:
        current[col] = 1.0  # collapse four features (44% of 9)
    result = taxi_drift.feature_drift(sample(seed=1), current, FEATURES)
    assert result['drift_detected'] is True


def test_thresholds_come_from_config_not_from_code(tmp_path):
    cfg = yaml.safe_load(open(taxi_drift.CONFIG_PATH))
    assert {'psi_threshold', 'ks_statistic_threshold', 'dataset_share_threshold'} <= cfg['drift'].keys()
    current = sample(seed=2, shift=70)
    strict = taxi_drift.feature_drift(sample(seed=1), current, FEATURES, cfg)
    cfg['drift']['psi_threshold'] = 1e6
    cfg['drift']['ks_statistic_threshold'] = 1.1
    lenient_path = tmp_path / 'lenient.yml'
    lenient_path.write_text(yaml.safe_dump(cfg))
    lenient = taxi_drift.feature_drift(sample(seed=1), current, FEATURES, taxi_drift.load_config(lenient_path))
    assert strict['drift_detected'] or strict['features']['pu_location_id']['drifted']
    assert lenient['drift_share'] == 0.0


def test_validation_quarantines_bad_rows_with_reason():
    raw = raw_data(300)
    raw.loc[:9, 'PULocationID'] = 9999      # out of bounds
    raw.loc[10:14, 'passenger_count'] = np.nan  # missing
    raw.loc[15:19, 'fare_amount'] = -5       # negative fare
    valid, quarantined = flow.prepare_with_quarantine(raw)
    assert len(valid) == 280 and len(quarantined) == 20
    assert quarantined['reason'].str.contains('pu_location_id').sum() == 10
    assert quarantined['reason'].str.contains('passenger_count').sum() == 5
    assert quarantined['reason'].str.contains('total_fare').sum() == 5


def test_schema_rejects_wrong_types_and_missing_values():
    frame = sample(10)
    frame['total_fare'] = 10.0
    frame.loc[3, 'vendor_id'] = np.inf
    valid, quarantined = taxi_validation.split_valid_invalid(frame)
    assert len(quarantined) == 1 and 3 in quarantined.index


def test_too_many_invalid_rows_fails_loudly():
    raw = raw_data(300)
    raw.loc[:100, 'PULocationID'] = 9999
    with pytest.raises(ValueError, match='20%'):
        flow.prepare(raw)
