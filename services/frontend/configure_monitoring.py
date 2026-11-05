"""Give demo viewers read access to the provisioned Grafana folder."""
import os
import time
import requests


def configure():
    session = requests.Session()
    session.auth = ('admin', os.getenv('GRAFANA_ADMIN_PASSWORD', 'admin'))
    base = os.getenv('GRAFANA_URL', 'http://grafana:3000')
    for attempt in range(60):
        try:
            response = session.get(f'{base}/api/search', params={'type': 'dash-folder'}, timeout=5)
            response.raise_for_status()
            folder = next((f for f in response.json() if f['title'] == 'NYC Taxi MLOps'), None)
            if folder:
                response = session.post(f"{base}/api/folders/{folder['uid']}/permissions", json={'items': [
                    {'role': 'Viewer', 'permission': 1}, {'role': 'Editor', 'permission': 2}
                ]}, timeout=10)
                response.raise_for_status()
                print('Grafana folder permissions configured for demo viewers')
                return
        except requests.RequestException:
            if attempt == 59:
                raise
        time.sleep(2)
    raise RuntimeError('Provisioned Grafana folder was not found')


if __name__ == '__main__':
    configure()
