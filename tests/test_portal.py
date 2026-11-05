from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'services/frontend/app.py'
FRONTEND = str(ROOT / 'services/frontend')


def test_login_then_story_home_then_logout(monkeypatch):
    monkeypatch.setenv('PORTAL_USERNAME', 'demo')
    monkeypatch.setenv('PORTAL_PASSWORD', 'test-password')
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value.startswith('NYC Taxi portal')
    app.text_input[0].set_value('demo')
    app.text_input[1].set_value('wrong')
    app.button[0].click().run()
    assert app.error
    app.text_input[1].set_value('test-password')
    app.button[0].click().run()
    assert not app.exception
    assert 'ML monitoring' in app.title[0].value        # landing page of the story
    app.sidebar.button[0].click().run()                  # sign out
    assert len(app.text_input) == 2


def test_no_password_fails_closed(monkeypatch):
    monkeypatch.delenv('PORTAL_PASSWORD', raising=False)
    app = AppTest.from_file(str(APP)).run()
    assert app.error
    assert len(app.button) == 0


PAGES = [('story', f) for f in ['home', 'normal', 'latency', 'drift', 'validation', 'outage', 'quality', 'business']] + \
        [('tools', f) for f in ['alerts', 'logs_traces', 'grafana', 'prefect', 'prometheus', 'mlflow_page', 'alertmanager']]


@pytest.mark.parametrize('module,function', PAGES)
def test_every_page_renders_without_backends(module, function, monkeypatch):
    """Pages must degrade gracefully (the outage scenario makes backends unavailable on purpose)."""
    monkeypatch.setenv('PREDICTION_API_URL', 'http://127.0.0.1:9')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://x:x@127.0.0.1:9/x')
    for name in ['PROMETHEUS_API_URL', 'ALERT_WEBHOOK_URL', 'LOKI_API_URL', 'TEMPO_API_URL', 'CONTROLLER_API_URL',
                 'SIMULATOR_API_URL', 'PREFECT_API_URL', 'ALERTMANAGER_API_URL']:
        monkeypatch.setenv(name, 'http://127.0.0.1:9')
    app = AppTest.from_string(
        f'import sys; sys.path.insert(0, {FRONTEND!r})\nimport {module}\n'
        f'{module}.PAGES = {{k: "x" for k in ["1_normal","2_latency","3_drift","4_validation","5_outage","6_quality","7_business"]}} if hasattr({module}, "PAGES") else None\n'
        f'import streamlit as st\nst.page_link = lambda *a, **k: None\n{module}.{function}()',
        default_timeout=30).run()
    assert not app.exception, app.exception
    assert app.title
