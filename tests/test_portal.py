from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / 'services/frontend/app.py'


def test_login_navigation_and_logout(monkeypatch):
    monkeypatch.setenv('PORTAL_USERNAME', 'demo')
    monkeypatch.setenv('PORTAL_PASSWORD', 'test-password')
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    assert len(app.sidebar.radio) == 0
    app.text_input[0].set_value('demo')
    app.text_input[1].set_value('wrong')
    app.button[0].click().run()
    assert app.error
    assert len(app.sidebar.radio) == 0
    app.text_input[1].set_value('test-password')
    app.button[0].click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == 'Démo guidée'
    app.sidebar.radio[0].set_value('Prefect').run()
    assert app.info
    app.sidebar.radio[0].set_value('Monitoring — Système').run()
    assert not app.exception
    assert 'l2-system-overview' in app.get('iframe')[0].proto.src
    app.sidebar.button[0].click().run()
    assert len(app.sidebar.radio) == 0
    assert len(app.text_input) == 2


def test_no_password_fails_closed(monkeypatch):
    monkeypatch.delenv('PORTAL_PASSWORD', raising=False)
    app = AppTest.from_file(str(APP)).run()
    assert app.error
    assert len(app.sidebar.radio) == 0
    assert len(app.button) == 0
