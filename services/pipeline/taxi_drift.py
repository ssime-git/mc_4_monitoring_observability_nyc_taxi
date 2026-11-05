"""Per-feature drift (PSI and KS) with thresholds read from config/monitoring.yml."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

CONFIG_PATH = Path(os.getenv('MONITORING_CONFIG', Path(__file__).resolve().parents[2] / 'config' / 'monitoring.yml'))
EPS = 1e-4


def load_config(path=None):
    return yaml.safe_load(Path(path or CONFIG_PATH).read_text())


def psi(reference, current, bins=10, categorical_max_unique=20):
    """Population Stability Index. Categorical-like features compare value frequencies,
    continuous ones compare shares inside reference quantile bins."""
    reference, current = np.asarray(reference, float), np.asarray(current, float)
    if len(np.unique(reference)) <= categorical_max_unique:
        categories = np.union1d(np.unique(reference), np.unique(current))
        ref = np.array([(reference == c).mean() for c in categories])
        cur = np.array([(current == c).mean() for c in categories])
    else:
        edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        ref = np.histogram(reference, edges)[0] / len(reference)
        cur = np.histogram(current, edges)[0] / len(current)
    ref, cur = np.clip(ref, EPS, None), np.clip(cur, EPS, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def feature_drift(reference: pd.DataFrame, current: pd.DataFrame, features, config=None):
    cfg = (config or load_config())['drift']
    per_feature = {}
    for name in features:
        score = psi(reference[name], current[name], cfg['psi_bins'], cfg['categorical_max_unique'])
        ks = float(stats.ks_2samp(reference[name], current[name]).statistic)
        per_feature[name] = {'psi': score, 'ks': ks,
                             'drifted': bool(score >= cfg['psi_threshold'] or ks >= cfg['ks_statistic_threshold'])}
    share = sum(f['drifted'] for f in per_feature.values()) / len(per_feature)
    return {'features': per_feature, 'drift_share': float(share),
            'drift_detected': bool(share >= cfg['dataset_share_threshold'])}
