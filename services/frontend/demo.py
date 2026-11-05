"""Presenter controls and live measurements, without leaving Streamlit."""
import os
import time
import requests
import streamlit as st
from metric_guides import diagram, render_guide

API = os.getenv('PREDICTION_API_URL', 'http://prediction_api:8000')
PROM = os.getenv('PROMETHEUS_API_URL', 'http://prometheus:9090')
SIM = os.getenv('SIMULATOR_API_URL', 'http://load_simulator:8010')
PREFECT = os.getenv('PREFECT_API_URL', 'http://prefect_server:4200/api')
PAYLOAD = dict(vendor_id=1, passenger_count=1., rate_code_id=1., pu_location_id=48,
               do_location_id=79, pickup_hour=14, pickup_dayofweek=2, pickup_month=1, pickup_weekday=1)


def call(method, url, **kwargs):
    response = requests.request(method, url, timeout=10, **kwargs)
    response.raise_for_status()
    return response.json()


def value(query):
    results = call('GET', f'{PROM}/api/v1/query', params={'query': query})['data']['result']
    if not results:
        return None
    number = float(results[0]['value'][1])
    return number if number == number else None


@st.fragment(run_every='5s')
def live():
    st.subheader('3 · Observer ce qui change')
    st.caption('Vraies mesures Prometheus · actualisation toutes les 5 secondes · les courbes peuvent mettre quelques collectes à réagir.')
    try:
        targets = call('GET', f'{PROM}/api/v1/targets')['data']['activeTargets']
        target = next((t for t in targets if t['labels'].get('job') == 'prediction_api'), None)
        if target and target['health'] == 'up':
            st.success('🟢 Prometheus collecte bien les métriques de la Prediction API.')
            st.caption(f"Dernière collecte : {target['lastScrape']} · endpoint : {target['scrapeUrl']}")
        else:
            st.error('Prometheus ne collecte pas la Prediction API : '+str(target.get('lastError', '') if target else 'cible absente'))
        cols = st.columns(4)
        queries = [('Prédictions réussies', 'sum(prediction_requests_total)', ''),
                   ('Prédictions / s', 'sum(rate(prediction_requests_total[1m]))', ''),
                   ('Rejets 422 / s', 'sum(rate(http_requests_total{status="422"}[1m])) or vector(0)', ''),
                   ('Latence p99', 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{endpoint="/fare_predict"}[1m])) by (le)) * 1000', ' ms')]
        for col, (label, query, suffix) in zip(cols, queries):
            result = value(query)
            col.metric(label, 'En attente' if result is None else f'{result:,.2f}{suffix}')
        end = time.time()
        data = call('GET', f'{PROM}/api/v1/query_range', params={
            'query': 'sum(rate(http_requests_total{endpoint="/fare_predict"}[1m]))',
            'start': end-300, 'end': end, 'step': 5})['data']['result']
        if data:
            import pandas as pd
            points = data[0]['values']
            frame = pd.DataFrame({'Heure': pd.to_datetime([p[0] for p in points], unit='s', utc=True),
                                  'Requêtes / s': [float(p[1]) for p in points]})
            st.line_chart(frame.set_index('Heure'), height=220)
        st.caption('Le total repart à zéro si l’API redémarre. Le débit est calculé sur la dernière minute ; il baisse progressivement après une pause.')
    except (requests.RequestException, KeyError, ValueError) as exc:
        st.warning(f'Mesures momentanément indisponibles : {exc}')
    try:
        simulator = call('GET', f'{SIM}/status')
        st.write(f"🚦 Simulateur **{'en marche' if simulator['running'] else 'en pause'}** · "
                 f"{simulator['sent']} envois · {simulator['succeeded']} succès · "
                 f"{simulator['rejected']} rejets 422 · {simulator['failed']} autres échecs")
    except requests.RequestException:
        st.info('Le service de simulation est indisponible ; les prédictions manuelles restent possibles.')
    if st.session_state.get('demo_run_id'):
        try:
            run = call('GET', f"{PREFECT}/flow_runs/{st.session_state.demo_run_id}")
            st.info(f"🧠 Entraînement « {run['name']} » : {run['state_type']}")
            if run['state_type'] == 'COMPLETED':
                scores = st.columns(3)
                for col, (label, query, suffix) in zip(scores, [
                    ('RMSE candidat', 'model_drift_rmse_current', ' $'),
                    ('RMSE référence', 'model_drift_rmse_reference', ' $'),
                    ('Variables en dérive', 'data_drift_share * 100', ' %'),
                ]):
                    number = value(query)
                    col.metric(label, 'En attente' if number is None else f'{number:.2f}{suffix}')
                st.caption('Dernières valeurs publiées par le pipeline. RMSE plus faible = meilleur score. '
                           'Au premier run, la RMSE de référence reprend celle du candidat ; il n’existe pas encore de comparaison de dérive.')
        except requests.RequestException as exc:
            st.warning(f'Statut de l’entraînement indisponible : {exc}')


def demo_page():
    st.title('🚕 Le labo du taxi')
    st.markdown('**Faire une prédiction, créer du trafic, puis observer : tout se pilote ici.**')
    diagram('Vous ou le simulateur envoyez un trajet', 'L’API calcule un prix et compte la requête',
            'Prometheus collecte les compteurs', 'Les mesures ci-dessous se mettent à jour')
    st.subheader('1 · Essayer une prédiction')
    st.caption('Un trajet d’exemple est prêt : départ zone 48, arrivée zone 79, un passager à 14 h.')
    if st.button('🚕 Calculer un prix', type='primary', use_container_width=True):
        try:
            result = call('POST', f'{API}/fare_predict', json=PAYLOAD)
            st.success(f"Prix estimé : {result['predicted_fare']:.2f} $. Regardez le compteur après la prochaine collecte.")
        except requests.RequestException as exc:
            st.warning(f'Prédiction impossible pour le moment : {exc}. Le premier entraînement est peut-être encore en cours.')
    st.subheader('2 · Jouer avec le trafic')
    st.markdown('Le simulateur est un client automatique de la même API. **Augmentez le trafic**, puis **ajoutez des requêtes invalides** pour voir les rejets apparaître.')
    with st.form('traffic_controls'):
        speed = st.slider('Rythme demandé (requêtes par seconde)', 1, 20, 2)
        errors = st.slider('Part de requêtes volontairement invalides (%)', 0, 100, 10)
        start = st.form_submit_button('▶ Démarrer / appliquer')
        pause = st.form_submit_button('⏸ Mettre en pause')
    if start or pause:
        try:
            call('PUT', f'{SIM}/settings', json={'running': not pause, 'requests_per_second': speed, 'error_rate': errors/100})
            st.success('Simulation mise en pause.' if pause else 'Consignes envoyées au simulateur.')
        except requests.RequestException as exc:
            st.error(f'Impossible de piloter le simulateur : {exc}')
    st.caption('La part invalide est une probabilité, pas un quota exact. Ces requêtes produisent des 422, pas des erreurs serveur 500. Le rythme réel dépend aussi du temps de réponse de l’API.')
    live()
    render_guide('Monitoring — Système')
    st.subheader('4 · Relancer un entraînement')
    diagram('Télécharger et vérifier les données', 'Comparer à la référence avec Evidently',
            'Entraîner un candidat', 'Garder le meilleur modèle dans MLflow')
    st.markdown('Avec les mêmes données, le candidat peut avoir le même score : **le champion reste alors en place**. '
                'L’entraînement est lancé en arrière-plan ; vous pouvez continuer la présentation.')
    if st.button('🧠 Lancer un entraînement'):
        try:
            deployment = call('GET', f'{PREFECT}/deployments/name/nyc-taxi-demo/demo')
            result = call('POST', f"{PREFECT}/deployments/{deployment['id']}/create_flow_run", json={})
            st.session_state.demo_run_id = result['id']
            st.success('Exécution demandée. Son statut apparaîtra dans les mesures en direct sous quelques secondes.')
        except requests.RequestException as exc:
            st.error(f'Impossible de lancer le flow : {exc}')
    render_guide('Monitoring — Diagnostic')
    st.caption('Pour approfondir : les pages de monitoring, les rapports Evidently et les interfaces Prefect/MLflow restent accessibles dans le menu.')
