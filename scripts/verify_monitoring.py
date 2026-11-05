"""Verify actual anonymous dashboard access and queries through Grafana."""
import time
import requests

BASE = 'http://localhost:3000'
session = requests.Session()


def verify():
    for uid in ['l1-executive', 'l2-system-overview', 'l3-deep-dive']:
        response = session.get(f'{BASE}/api/dashboards/uid/{uid}', timeout=10)
        response.raise_for_status()
        dashboard = response.json()['dashboard']
        ids = [p['id'] for p in dashboard['panels']]
        assert len(ids) == len(set(ids)), 'Panel IDs must be unique'
        for panel in dashboard['panels']:
            if panel.get('datasource', {}).get('type') == 'tempo':
                for target in panel.get('targets', []):
                    response = session.get(f'{BASE}/api/datasources/proxy/uid/tempo/api/search',
                                           params={'q': target['query'], 'limit': 1}, timeout=20)
                    response.raise_for_status()
                    assert response.json().get('traces'), panel['title']
            if panel.get('datasource', {}).get('type') != 'prometheus':
                continue
            for target in panel.get('targets', []):
                query = dict(target, datasource={'uid': 'prometheus', 'type': 'prometheus'},
                             instant=True, range=False, intervalMs=5000, maxDataPoints=100)
                response = session.post(f'{BASE}/api/ds/query', json={
                    'from': str(int((time.time()-300)*1000)), 'to': str(int(time.time()*1000)),
                    'queries': [query]}, timeout=20)
                response.raise_for_status()
                result = response.json()['results'][target['refId']]
                assert not result.get('error'), (panel['title'], result)
                assert any(frame.get('data', {}).get('values', [[]])[0] for frame in result.get('frames', [])), panel['title']
        print(f'{uid}: anonymous access and all Prometheus queries OK')
    for uid, path, params in [
        ('loki', '/loki/api/v1/query_range', {'query': '{service="prediction_api"}', 'limit': 1}),
        ('tempo', '/api/search', {'q': '{ .service.name = "prediction_api" }', 'limit': 1}),
    ]:
        response = session.get(f'{BASE}/api/datasources/proxy/uid/{uid}{path}', params=params, timeout=20)
        response.raise_for_status()
        body = response.json()
        assert body.get('traces') if uid == 'tempo' else body['data']['result'], body
        print(f'{uid}: data available through Grafana proxy')


if __name__ == '__main__':
    verify()
