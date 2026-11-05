import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize('page', ['Grafana', 'Prometheus', 'MLflow', 'Evidently reports'])
def test_guides_render_and_each_metric_can_be_selected(page):
    app = AppTest.from_string(
        'from services.frontend.metric_guides import render_guide\n'
        f'render_guide({page!r})'
    ).run()
    assert not app.exception
    assert app.expander
    assert len(app.get('html')) >= 1
    for option in app.selectbox[0].options:
        app.selectbox[0].set_value(option).run()
        assert not app.exception
        assert any('How to read it' in message.value for message in app.info)
