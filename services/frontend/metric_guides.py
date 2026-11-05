"""Plain-language explanations tied to the metrics actually exported by the demo."""
from html import escape
import streamlit as st


def diagram(*steps):
    boxes = '<span aria-hidden="true">→</span>'.join(
        f'<div class="metric-step">{escape(step)}</div>' for step in steps)
    st.html('''<style>
    .metric-flow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 20px}
    .metric-step{flex:1;min-width:125px;padding:14px;border:1px solid #6483a5;
    border-top:3px solid #f08c46;border-radius:8px;font-size:14px}
    </style><div class="metric-flow" role="group" aria-label="Comment la mesure est obtenue">'''+boxes+'</div>')


BUSINESS = [
    ('Prédictions servies', 'On ajoute 1 après chaque prédiction réussie.',
     '1 200 signifie 1 200 prix calculés depuis le démarrage de cette API. Les échecs sont exclus.'),
    ('Prédictions par minute', 'Prometheus mesure la vitesse d’augmentation du compteur sur 1 minute, puis multiplie par 60.',
     '120/min indique environ 2 prédictions réussies par seconde. Le simulateur génère aussi du trafic.'),
    ('Prix moyen prédit', 'Somme des prix prédits sur 5 minutes ÷ nombre de prédictions sur ces 5 minutes.',
     'Une hausse peut venir de trajets différents ou d’un changement de modèle ; elle ne prouve pas une meilleure précision.'),
    ('Somme des prix prédits', 'On additionne les prix renvoyés par le modèle.',
     'C’est un indicateur de volume simulé, pas un chiffre d’affaires réellement encaissé.'),
    ('Distribution des prix', 'Chaque prix alimente des compteurs par seuil : 5 $, 10 $, 15 $, etc.',
     'Permet de voir si les prix se concentrent ou se déplacent vers des valeurs élevées. Les compteurs _bucket sont cumulatifs.'),
    ('Disponibilité des services', 'Prometheus réussit-il à lire /metrics ? up vaut 1 si oui, 0 sinon.',
     'up=1 ne garantit pas qu’une prédiction fonctionne. « Pipeline metrics » teste Pushgateway, pas l’exécution du flow.'),
    ('Modèle chargé et temps de chargement', 'L’API indique 1 après chargement et mesure le temps de téléchargement + désérialisation du modèle.',
     '0 signifie qu’aucun modèle n’a été chargé. La durée est celle du dernier chargement, pas la latence d’une prédiction.'),
    ('Trajets et dérive', 'Le flow publie le nombre total de lignes SQL utilisées et le résultat Evidently.',
     'Ce sont les valeurs du dernier flow qui a publié ses métriques ; elles ne changent pas à chaque prédiction.'),
]
SYSTEM = [
    ('Débit HTTP', 'Le middleware compte les requêtes par route, méthode et code HTTP. rate estime leur nombre par seconde.',
     'Comprend aussi /health et /metrics. Le débit total HTTP est donc différent du nombre de prédictions.'),
    ('Codes HTTP', 'Chaque réponse est classée par son code.',
     '200 : succès ; 422 : données de requête invalides ; 503 : modèle indisponible ; 500 : erreur pendant l’inférence.'),
    ('Taux d’erreurs 5xx', 'Nombre de réponses 500–599 ÷ nombre total de réponses, dans la même fenêtre.',
     '5 erreurs sur 100 réponses = 5 %. Les 422 sont exclus de ce taux. Sans trafic, zéro n’est pas une preuve de bon fonctionnement.'),
    ('Latence p50 / p90 / p99', 'L’API mesure la durée des requêtes et les range par seuil. Prometheus estime les percentiles sur 5 minutes.',
     'p99=200 ms : environ 99 % des requêtes sont plus rapides. Il reste environ 1 % plus lent. Ce n’est ni le maximum ni une moyenne.'),
    ('Logs', 'Promtail collecte les lignes écrites par les conteneurs et les envoie à Loki.',
     'À lire pour comprendre le contexte d’une erreur. Un panneau de logs ERROR vide peut simplement signifier aucune erreur enregistrée.'),
    ('Traces', 'OpenTelemetry enregistre des opérations chronométrées, liées par un identifiant de trace, dans Tempo.',
     'Une trace aide à localiser l’étape lente d’une requête. Ici l’instrumentation concerne la Prediction API, pas tout le pipeline Prefect.'),
]
DIAGNOSTIC = [
    ('Latence d’inférence', 'Chronométrage de model.predict uniquement, puis estimation des percentiles.',
     'Comparer à la latence HTTP : celle-ci inclut davantage de travail. Une différence ne mesure pas directement le réseau du navigateur.'),
    ('Erreurs de prédiction par type', 'L’API incrémente un compteur lorsqu’un chargement ou une inférence échoue.',
     'Les requêtes rejetées par la validation FastAPI en 422 ne passent pas dans cette logique : elles apparaissent dans les codes HTTP.'),
    ('Lignes d’entraînement', 'Nombre de lignes de training_trips lues par le dernier entraînement.',
     'Rejouer les mêmes paramètres d’ingestion ne doit pas augmenter ce nombre. Le modèle utilise toutes les lignes SQL, pas seulement le lot courant.'),
    ('RMSE du candidat et du champion', 'Sur le même jeu de validation, calculer l’erreur de chaque prix, la mettre au carré, faire la moyenne, puis prendre la racine.',
     'Plus bas est meilleur. Une RMSE de 8 $ exprime une échelle d’erreur qui pénalise fortement les grosses erreurs ; ce n’est pas l’erreur moyenne absolue.'),
    ('R² dans MLflow', 'Comparer les erreurs quadratiques du modèle à celles d’une prédiction constante égale à la moyenne des prix de validation.',
     '1 : prédiction parfaite ; 0 : aussi bon que cette moyenne ; négatif : moins bon. R² n’est pas un pourcentage de prédictions correctes.'),
    ('Part de variables en dérive', 'Evidently compare les distributions des 9 features : nombre signalé en dérive ÷ 9.',
     '3 variables sur 9 donnent environ 33 %. Ce n’est pas 33 % de trajets faux. Consulter le rapport pour connaître les variables et tests utilisés.'),
    ('Dérive détectée', 'Décision globale 0/1 produite par DataDriftPreset à partir de ses tests et seuils.',
     '0 : pas de dérive signalée par ces tests ; 1 : dérive signalée. Cela ne prouve pas à lui seul une baisse de qualité des prédictions.'),
    ('Alertes', 'Prometheus évalue des règles et vérifie si une condition persiste pendant la durée configurée.',
     'Pending : condition en cours d’observation ; firing : durée dépassée. Aucun envoi externe n’est configuré. Le panneau Grafana Alert List concerne les alertes Grafana ; consulter Prometheus /alerts pour les règles de cette démo.'),
]


def table(rows):
    titles = [row[0] for row in rows]
    selected = st.selectbox('Quelle métrique voulez-vous comprendre ?', titles, key='guide_' + titles[0])
    title, calculation, reading = rows[titles.index(selected)]
    st.markdown(f'#### {title}')
    st.markdown(f'**Comment elle est obtenue :** {calculation}')
    st.info(f'**Comment la lire :** {reading}')


def render_guide(page):
    if page not in ('Monitoring — Métier', 'Monitoring — Système', 'Monitoring — Diagnostic',
                    'Prometheus', 'MLflow', 'Rapports Evidently'):
        return
    label = 'Comprendre les scores du modèle et la dérive' if page in ('Monitoring — Diagnostic', 'MLflow', 'Rapports Evidently') else 'Comprendre les métriques : calcul, schémas et interprétation'
    with st.expander(label, expanded=False):
        st.caption('Exemples pédagogiques : les nombres ci-dessous ne sont pas les valeurs en direct du dashboard.')
        if page == 'Monitoring — Métier':
            st.subheader('Du prix calculé aux indicateurs métier')
            diagram('Une prédiction réussit : 18 $', 'Compteur +1 ; somme +18 $ ; histogramme',
                    'Prometheus lit /metrics toutes les 5 s', 'Grafana affiche totaux et tendances')
            table(BUSINESS)
        elif page in ('Monitoring — Système', 'Prometheus'):
            st.subheader('Compter les requêtes et mesurer leur durée')
            diagram('Requête reçue', 'Chronométrage et code de réponse',
                    'Compteurs et histogrammes dans /metrics', 'Prometheus calcule débits, taux et percentiles')
            table(SYSTEM)
            st.markdown('**Exemple :** 100 requêtes, dont 5 réponses 500 → **5 % d’erreurs serveur**. '
                        'Une durée de **0,2 s = 200 ms**. Les percentiles sont estimés à partir des seuils des histogrammes ; '
                        'sur très peu de requêtes, ils sont peu représentatifs.')
            if page == 'Prometheus':
                st.code('rate(prediction_requests_total[1m]) * 60', language='promql')
                st.caption('Lire : estimer le rythme du compteur sur la dernière minute, puis l’exprimer par minute.')
        else:
            st.subheader('Mesurer le modèle et suivre les données')
            diagram('Lot courant validé', 'Evidently compare à la référence du champion',
                    'Candidat et champion évalués sur les mêmes trajets', 'MLflow conserve résultats et artefacts')
            table(DIAGNOSTIC)
            st.markdown('**Exemple RMSE :** erreurs de +3 $ et −4 $ → carrés 9 et 16 → moyenne 12,5 → '
                        'racine ≈ **3,54 $**. Ce calcul donne plus de poids aux grandes erreurs.')
            st.markdown('**Décision :** la qualité des données peut bloquer l’ingestion. La dérive seule ne bloque pas '
                        'l’entraînement. Chaque flow valide entraîne un candidat ; il remplace le champion uniquement si sa RMSE est inférieure.')
            diagram('Premier run sans champion', 'Créer une référence : pas de comparaison de dérive',
                    'Runs suivants : comparer au lot du champion précédent')
            st.info('Lors du premier run, la dérive publiée à 0 signifie « référence initiale », pas « absence de dérive démontrée ». '
                    'Pushgateway conserve le dernier résultat publié : une valeur visible peut provenir d’un run ancien. '
                    'Vérifier la date et le statut dans Prefect. Un modèle déjà en mémoire peut rester utilisable si MLflow devient indisponible.')
            st.caption('La validation aléatoire 80/20 est adaptée à la démonstration. Avec de nouveaux lots, elle peut inclure '
                       'des trajets déjà vus par le champion ; elle ne remplace pas un jeu de test temporel indépendant.')
        st.markdown('**Lire les écrans sans piège :** les compteurs de l’API repartent à zéro au redémarrage. '
                    'Prometheus tient compte de ces remises à zéro pour calculer `rate`. Les métriques du flow sont des jauges du dernier run publié. '
                    '« No data » peut indiquer une absence de mesures ou une fenêtre trop courte : ce n’est pas automatiquement zéro.')
        diagram('Un nombre indique quoi surveiller', 'Les logs donnent le contexte',
                'Les traces situent la durée des opérations')
