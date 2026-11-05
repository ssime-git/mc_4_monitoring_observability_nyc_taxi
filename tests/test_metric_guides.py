import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize('page', [
    'Monitoring — Métier', 'Monitoring — Système', 'Monitoring — Diagnostic',
    'Prometheus', 'MLflow', 'Rapports Evidently',
])
def test_guides_render_and_each_metric_can_be_selected(page):
    app = AppTest.from_string(
        'from services.frontend.metric_guides import render_guide\n'
        f'render_guide({page!r})'
    ).run()
    assert not app.exception
    assert app.expander
    assert len(app.get('html')) >= 2
    for option in app.selectbox[0].options:
        app.selectbox[0].set_value(option).run()
        assert not app.exception
        assert any('Comment la lire' in message.value for message in app.info)
    assert any('No data' in m.value for m in app.markdown)
