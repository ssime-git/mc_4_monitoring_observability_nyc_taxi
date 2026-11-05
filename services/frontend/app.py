import requests
import streamlit as st
from datetime import datetime
import os
import hmac
from urllib.parse import urlsplit
import streamlit.components.v1 as components
from metric_guides import render_guide
from demo import demo_page

def taxi_api_call(payload):
    # Use environment variable for the prediction API URL
    api_url = os.getenv("PREDICTION_API_URL", "http://prediction_api:8000")
    url = f"{api_url}/fare_predict"
    headers = {'accept': 'application/json'}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response
    except Exception as e:
        st.error(f"Error connecting to API: {e}")
        return None

def prediction_page():
    st.title("🚖 NYC Taxi Fare Prediction (MLOps Edition)")
    st.markdown("""
    This app uses a Random Forest model tracked by **MLflow**, with data stored in **PostgreSQL**.
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Trip Details")
        vendor_id = st.selectbox("Vendor ID", [1, 2], index=0)
        passenger_count = st.number_input("Passenger Count", min_value=1, max_value=10, value=1)
        rate_code_id = st.number_input("Rate Code ID", min_value=1.0, max_value=6.0, value=1.0)
        
    with col2:
        st.subheader("Location IDs")
        pu_location_id = st.number_input("Pickup Location ID", min_value=1, max_value=265, value=1)
        do_location_id = st.number_input("Dropoff Location ID", min_value=1, max_value=265, value=1)

        st.subheader("Time Information")
        pickup_datetime = st.date_input("Pickup Date", datetime.today())
        pickup_time = st.time_input("Pickup Time", datetime.now().time())

    if st.button("Predict Fare", use_container_width=True):
        pickup_dt = datetime.combine(pickup_datetime, pickup_time)
        
        payload = {
            "vendor_id": vendor_id,
            "passenger_count": float(passenger_count),
            "rate_code_id": rate_code_id,
            "pu_location_id": pu_location_id,
            "do_location_id": do_location_id,
            "pickup_hour": pickup_dt.hour,
            "pickup_dayofweek": pickup_dt.weekday(),
            "pickup_month": pickup_dt.month,
            "pickup_weekday": 1 if pickup_dt.weekday() < 5 else 0
        }

        with st.spinner("Getting prediction..."):
            response = taxi_api_call(payload)

            if response is not None and response.status_code == 200:
                prediction = response.json()
                st.balloons()
                st.success(f"### Predicted Fare Amount: ${prediction['predicted_fare']:.2f}")
            elif response is not None:
                st.error(f"API Error: {response.status_code} - {response.text}")
            else:
                st.error("Failed to receive response from the API.")

def require_login():
    if st.session_state.get("authenticated", False):
        return
    st.title("Connexion au portail NYC Taxi")
    expected_user = os.getenv("PORTAL_USERNAME", "demo")
    expected_password = os.getenv("PORTAL_PASSWORD", "")
    if not expected_password:
        st.error("Configurez PORTAL_PASSWORD pour activer le portail.")
        st.stop()
    with st.form("login"):
        username = st.text_input("Identifiant", key="login_username")
        password = st.text_input("Mot de passe", type="password", key="login_password")
        submitted = st.form_submit_button("Se connecter", use_container_width=True)
    if submitted:
        valid_user = hmac.compare_digest(username.encode(), expected_user.encode())
        valid_password = hmac.compare_digest(password.encode(), expected_password.encode())
        if valid_user and valid_password:
            st.session_state["authenticated"] = True
            del st.session_state["login_username"]
            del st.session_state["login_password"]
            st.rerun()
        st.error("Identifiant ou mot de passe incorrect.")
    st.stop()


def embedded_page(title, url):
    st.title(title)
    render_guide(title)
    if not url:
        st.info("Prefect sera disponible ici une fois déployé et PREFECT_UI_URL configurée.")
        return
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        st.error("L’URL configurée doit être une adresse HTTP ou HTTPS accessible au navigateur.")
        return
    components.iframe(url, height=900, scrolling=True)
    st.link_button("Ouvrir dans un onglet séparé", url)


def reports_page():
    from mlflow import MlflowClient
    from mlflow.artifacts import load_text
    import mlflow
    st.title("Qualité et dérive — Evidently")
    render_guide("Rapports Evidently")
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow_server:5000"))
    client = MlflowClient()
    try:
        experiment = client.get_experiment_by_name("NYC_Taxi_Fare_Prediction_MinIO")
        if experiment is None:
            st.info("Le premier pipeline n’a pas encore produit de rapport.")
            return
        runs = client.search_runs([experiment.experiment_id], max_results=30,
                                  order_by=["attributes.start_time DESC"])
        if not runs:
            st.info("Aucune exécution disponible.")
            return
        chosen = st.selectbox("Exécution", runs,
                              format_func=lambda r: f"{r.info.run_id[:12]} · {r.info.status}")
        if chosen.info.status != "FINISHED":
            st.info("Le rapport sera disponible après la fin réussie de cette exécution.")
            return
        summary = load_text(f"runs:/{chosen.info.run_id}/drift/summary.json")
        import json
        summary = json.loads(summary)
        if summary["baseline"]:
            st.info("Première exécution : création de la référence, sans comparaison de dérive.")
        else:
            st.metric("Part de variables en dérive", f"{summary['drift_share']:.0%}")
            report = load_text(f"runs:/{chosen.info.run_id}/drift/drift.html")
            st.download_button("Télécharger le rapport HTML", report,
                               file_name=f"evidently-{chosen.info.run_id}.html", mime="text/html")
            components.html(report, height=900, scrolling=True)
    except Exception as exc:
        st.error(f"Rapports indisponibles : {exc}")


def main():
    st.set_page_config(page_title="Portail NYC Taxi", layout="wide")
    require_login()
    with st.sidebar:
        st.title("NYC Taxi")
        page = st.radio("Navigation", [
            "Démo guidée", "Prédiction", "Prefect", "Monitoring — Métier",
            "Monitoring — Système", "Monitoring — Diagnostic", "Rapports Evidently", "Prometheus", "MLflow",
        ])
        if st.button("Se déconnecter", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    grafana = os.getenv("GRAFANA_UI_URL", "http://localhost:3000").rstrip("/")
    pages = {
        "Prefect": os.getenv("PREFECT_UI_URL", ""),
        "Monitoring — Métier": f"{grafana}/d/l1-executive?orgId=1&kiosk",
        "Monitoring — Système": f"{grafana}/d/l2-system-overview?orgId=1&kiosk",
        "Monitoring — Diagnostic": f"{grafana}/d/l3-deep-dive?orgId=1&kiosk",
        "Prometheus": os.getenv("PROMETHEUS_UI_URL", "http://localhost:9090"),
        "MLflow": os.getenv("MLFLOW_UI_URL", "http://localhost:5000"),
    }
    if page == "Démo guidée":
        demo_page()
    elif page == "Prédiction":
        prediction_page()
    elif page == "Rapports Evidently":
        reports_page()
    else:
        embedded_page(page, pages[page])


if __name__ == "__main__":
    main()
