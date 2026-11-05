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
