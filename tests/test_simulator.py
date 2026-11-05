from fastapi.testclient import TestClient
from services.load_simulator import simulator


def test_simulator_control_validation_and_status(monkeypatch):
    monkeypatch.setitem(simulator.state, 'running', False)
    with TestClient(simulator.app) as client:
        assert client.get('/status').json()['running'] is False
        result = client.put('/settings', json={'running': False, 'requests_per_second': 5, 'error_rate': .5})
        assert result.status_code == 200
        assert result.json()['error_rate'] == .5
        assert client.put('/settings', json={'running': True, 'requests_per_second': 500, 'error_rate': 0}).status_code == 422
        assert client.get('/status').json()['running'] is False


def test_label_bias_is_optional_and_applied_to_synthetic_ground_truth(monkeypatch):
    monkeypatch.setitem(simulator.state, 'running', False)
    simulator.pending_feedback.clear()
    with TestClient(simulator.app) as client:
        result = client.put('/settings', json={'running': False, 'requests_per_second': 5, 'error_rate': 0, 'label_bias': 0.5})
        assert result.json()['label_bias'] == 0.5
        assert client.put('/settings', json={'running': False, 'requests_per_second': 5, 'error_rate': 0}).json()['label_bias'] == 0.5
    simulator.schedule_feedback('req-1', 10.0)
    _, request_id, actual = simulator.pending_feedback[0]
    assert request_id == 'req-1' and 11 < actual < 19
