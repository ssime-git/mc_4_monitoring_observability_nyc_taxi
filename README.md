# NYC Taxi — monitoring et observabilité

Démonstration MLOps entièrement démarrée par Docker Compose : téléchargement de
vraies données NYC TLC, contrôles qualité, stockage PostgreSQL, rapport de dérive,
entraînement Random Forest et enregistrement dans MLflow. Les artefacts sont
stockés dans MinIO ; aucun modèle préentraîné n'est embarqué dans le dépôt.

## Lancer la démonstration

Prérequis : Docker Compose et accès au daemon Docker. Internet est nécessaire
pour les images et le téléchargement du Parquet TLC.

```bash
cp .env.example .env
# Renseigner PORTAL_PASSWORD dans .env.
docker compose up --build -d
```

Ouvrir **http://localhost:8501** et se connecter avec `demo` et le mot de passe
choisi. Le portail rassemble prédiction, Prefect, dashboards Grafana, rapports
Evidently, Prometheus et MLflow. Au premier lancement, attendre la fin du flow
Prefect : l'API de prédiction répond 503 tant que le champion n'est pas disponible.

La première construction compile MinIO depuis un tag du code officiel : ses
anciennes images publiques ne sont plus téléchargeables sur les registres
historiques. Cette étape peut prendre plusieurs minutes, puis bénéficie du cache.

## Architecture

- **Prefect Server / UI** (`4200`) : déploiement, planning et état des exécutions.
  Une base SQLite persistante est utilisée pour cette démo locale mono-serveur.
- **Bootstrap** : crée le work pool Docker, le déploiement et le premier run.
- **monitoring_init** : donne au rôle Viewer l’accès au dossier des dashboards Grafana.
- **Worker Docker** : interroge Prefect et lance un conteneur par flow. Les tâches
  tournent dans ce conteneur, supprimé automatiquement à la fin (`auto_remove`).
  Le worker utilise le socket Docker de l'hôte.
- **Flow** : télécharge le Parquet TLC de janvier 2024, prépare jusqu'à
  `DEMO_ROWS=5000` lignes, valide et charge PostgreSQL sans doublons à la relance.
  Compare les nouvelles données à la référence du champion, puis entraîne et
  évalue un candidat. Un planning quotidien permet de rejouer le flow ; les
  paramètres année/mois/limite sont modifiables dans Prefect.
- **PostgreSQL** (`5432`) : données d'entraînement et identifiants d'ingestion.
- **MLflow** (`5000`) : expériences, métriques, versions et alias `champion`.
  Les métadonnées sont dans une SQLite persistante distincte de Prefect.
- **MinIO** (`9000`, console `9001`) : bucket privé `mlflow-artifacts`, créé
  automatiquement. MLflow sert de proxy pour les transferts S3 : les clients
  n'ont ni volume modèle partagé ni identifiants S3 à gérer.
- **Prediction API** (`8000`) : charge le champion depuis MLflow, vérifie les
  changements toutes les 60 secondes et expose `/fare_predict` et `/metrics`.
- **Streamlit** (`8501`) : authentification et navigation de démonstration.
- **Load simulator** : génère le trafic de prédiction.
- **Prometheus** (`9090`) et **Pushgateway** : métriques API et dernier résultat
  du pipeline éphémère. Les règles d'alerte sont évaluées dans Prometheus.
- **Promtail → Loki** (`3100`) : logs Docker. Les logs des flows sont également
  conservés dans Prefect ; l'auto-suppression peut précéder la collecte Promtail.
- **OpenTelemetry → Tempo** (`4317`/`3200`) : traces de la Prediction API.
- **Grafana** (`3000`) : trois dashboards métier, système et diagnostic.

Les services partagent le réseau `nyc-taxi-ml`. Les anciennes Data API,
Training API et boucles ETL ont été remplacées par les tâches Prefect.

## Qualité, dérive et modèles

Des colonnes absentes, moins de 100 lignes valides ou plus de 20 % de lignes
invalides bloquent l'ingestion. Une dérive seule ne bloque pas l'entraînement.
Le candidat et le champion sont comparés sur le même échantillon de validation
(seed fixe, suffisant pour cette démo ; une évaluation temporelle indépendante
serait préférable pour un cas réel). Le candidat n'est promu que si sa RMSE
est inférieure, ou si aucun champion n'existe.

Chaque run MLflow conserve la référence Parquet, les métriques et le résumé de
dérive. Dès le deuxième run, Evidently produit aussi les rapports HTML et JSON,
consultables et téléchargeables dans Streamlit. La référence utilisée est celle
du champion précédent ; elle ne change pas tant que ce champion reste en place.

Les volumes conservent données SQL, métadonnées et objets MinIO après l'arrêt.
`docker compose down` les préserve ; `docker compose down -v` les efface et permet
une démonstration entièrement neuve.

## Comprendre les pipelines

Les flèches sont libellées pour distinguer les **appels** des **données
échangées**. L'ingestion est pilotée par le flow : NYC TLC ne pousse pas les
fichiers vers notre infrastructure.

### Vue d'ensemble : qui pilote quoi ?

```mermaid
flowchart TB
    User[Utilisateur] --> Portal["Streamlit :8501<br/>Connexion et navigation"]
    Portal -->|Demande de prédiction| API["Prediction API :8000"]
    Portal -.->|Iframe dans le navigateur| UI["Prefect UI :4200"]
    Portal -.->|Iframes dans le navigateur| Views["Grafana / Prometheus / MLflow"]
    subgraph Control[Orchestration permanente]
        UI --> Server[Prefect Server / API]
        Boot[Bootstrap] -->|Déploie et soumet le premier run| Server
        Worker[Docker worker] -->|Interroge le work pool| Server
        Worker -->|Demande la création du conteneur| Docker[Moteur Docker de l'hôte]
    end
    subgraph Execution[Un conteneur éphémère par flow]
        Flow["Flow nyc-taxi-demo<br/>Ingestion → Dérive → Entraînement"]
    end
    Docker -->|Lance| Flow
    Flow -->|Télécharge le Parquet| TLC[NYC TLC]
    Flow -->|Écrit et lit les données préparées| DB[(PostgreSQL métier)]
    Flow -->|Enregistre modèles et rapports| ML[MLflow]
    ML -->|Stocke et lit les artefacts via S3| S3[(MinIO)]
    API -->|Résout champion et télécharge le modèle| ML
    Flow -.->|États et logs du run| Server
    Portal -->|Charge les rapports Evidently via MLflow| ML
```

Le worker reste actif entre les exécutions. Il **ne contient pas les tâches** :
il lance un autre conteneur utilisant l'image `nyc-taxi-pipeline:demo`. Les tâches
`ingest`, `check_drift` et `train_and_register` s'y exécutent dans cet ordre.

### Au lancement de la stack

```mermaid
flowchart TB
    Start["docker compose up --build -d"] --> Build[Construire les images avec uv.lock et compiler MinIO]
    Build --> Infra["Démarrer PostgreSQL, MinIO, Prefect et l'observabilité"]
    Infra --> Permissions["monitoring_init configure les permissions Viewer de Grafana"]
    Infra --> Bucket["minio_init crée le bucket mlflow-artifacts"]
    Bucket --> ML[MLflow démarre avec son proxy S3]
    ML --> Ready{"PostgreSQL, MLflow et Prefect healthy ?"}
    Infra --> Ready
    Ready -->|Oui| Bootstrap["Bootstrap : work pool taxi-docker et déploiement demo"]
    Ready -->|Non| Wait[Le bootstrap attend les dépendances]
    Bootstrap --> Submit[Soumettre le premier run et planifier toutes les 24 h]
    Submit --> Worker[Démarrer le worker Docker]
    Worker --> Run[Lancer le premier conteneur de flow]
    ML --> Serving["Démarrer Prediction API, Streamlit et le simulateur"]
    Serving --> NoModel["Prédiction : 503 tant qu'aucun modèle ne peut être chargé"]
    Run --> Champion[Entraîner et promouvoir le premier champion]
    Champion --> Load[La Prediction API charge le champion depuis MLflow]
    Load --> Available[Prédictions disponibles dans Streamlit]
```

`minio_init` et `prefect_bootstrap` sont des services ponctuels : leur état
**Exited (0)** signifie qu'ils ont terminé correctement. Le démarrage du portail
n'attend pas la fin de l'entraînement. Sur un redémarrage avec volumes conservés,
un champion déjà enregistré peut être chargé immédiatement.

### Cycle de vie d'une exécution Prefect

```mermaid
sequenceDiagram
    actor U as Utilisateur
    participant P as Prefect Server
    participant W as Docker worker
    participant D as Docker daemon
    participant F as Conteneur du flow
    U->>P: Lancement manuel depuis l'UI
    Note over P: Ou premier run du bootstrap / planning quotidien
    W->>P: Interroger le work pool taxi-docker
    P-->>W: Run prêt à exécuter
    W->>D: Créer et lancer le conteneur du flow
    D->>F: Exécuter le code du déploiement
    F->>P: États des tâches et logs
    Note over F: ingest → check_drift → train_and_register
    F->>P: État final du flow
    F-->>D: Fin du processus
    D->>D: Supprimer le conteneur avec auto_remove
    Note over P,W: Serveur et worker restent actifs
```

Le déploiement limite à **une exécution simultanée**. Une erreur remonte dans
Prefect ; `ingest` dispose de trois nouvelles tentatives espacées de dix secondes.
Les tâches de dérive et d'entraînement n'ont pas de retry automatique configuré.
Les artefacts déjà écrits dans MinIO et les données SQL survivent au conteneur.

### Ingestion : du Parquet aux données d'entraînement

```mermaid
flowchart TB
    Input["Paramètres : year, month, limit"] --> Download["Le flow télécharge le Parquet mensuel depuis NYC TLC"]
    Download --> Temp[Parquet dans un répertoire temporaire du conteneur]
    Temp --> Read[Lire les premières lignes, au maximum limit]
    Read --> Prepare["Normaliser les colonnes, calculer les variables temporelles et total_fare"]
    Prepare --> Columns{Colonnes requises présentes ?}
    Columns -->|Non| Fail[Erreur de validation : ingestion en échec]
    Columns -->|Oui| Quality{"Au moins 80 % de lignes valides<br/>et au moins 100 lignes après filtrage ?"}
    Quality -->|Non| Fail
    Quality -->|Oui| Key["Calculer la clé de lot : URL + limite + version de préparation"]
    Key --> Seen{Clé déjà dans ingestion_batches ?}
    Seen -->|Oui| Skip[Ne pas réinsérer le lot]
    Seen -->|Non| Store["Transaction SQL : écrire training_trips et ingestion_batches"]
    Skip --> Current[Retourner les données courantes pour le contrôle de dérive]
    Store --> Current
```

Le fichier brut temporaire est supprimé après lecture. PostgreSQL conserve les
**variables préparées**, pas le fichier brut. Rejouer les mêmes paramètres ne
réinsère pas le lot ; changer la limite crée une autre clé et peut produire un
échantillon qui recoupe les lignes déjà stockées.

### Dérive, entraînement et promotion du modèle

```mermaid
flowchart TB
    Current[Données courantes validées] --> Champion{Champion déjà enregistré ?}
    Champion -->|Non| Baseline["Premier run : baseline=true, sans rapport comparatif"]
    Champion -->|Oui| Reference[Charger le Parquet de référence du run du champion via MLflow]
    Reference --> Drift[Comparer les features avec Evidently]
    Current --> Drift
    Drift --> Reports["Produire drift.html, drift.json et les scores"]
    Baseline --> TrainData[Lire toutes les données de training_trips]
    Reports --> TrainData
    TrainData --> Split["Découpage train 80 % / validation 20 %, seed 42"]
    Split --> Fit[Entraîner un candidat Random Forest]
    Fit --> Evaluate[Calculer RMSE et R² sur la validation]
    Evaluate --> Compare[Évaluer aussi le champion sur cette même validation, s'il existe]
    Compare --> Register[Enregistrer le candidat comme nouvelle version MLflow]
    Register --> Persist["Sauvegarder métriques, résumé, rapports disponibles et référence courante dans MinIO via MLflow"]
    Persist --> Better{"Aucun champion<br/>ou RMSE candidat inférieure ?"}
    Better -->|Oui| Promote[Déplacer l'alias champion vers le candidat]
    Better -->|Non, y compris égalité| Keep[Conserver le champion existant]
    Promote --> Metrics[Publier les métriques du pipeline vers Pushgateway]
    Keep --> Metrics
```

**Actuellement chaque flow qui passe l'ingestion entraîne un candidat**, même
sans dérive. La dérive est un indicateur, pas une condition de déclenchement ou de
blocage. Le seuil qualité se trouve dans l'ingestion. Les rapports comparatifs
apparaissent dès qu'un champion et sa référence sont disponibles.

La référence de chaque candidat est l'échantillon courant ; le modèle, lui, est
entraîné sur l'ensemble des données SQL. Le contrôle suivant reprend la
référence du **champion**, qui reste stable lorsqu'un candidat n'est pas promu.
La promotion arrive après l'enregistrement des artefacts. Une erreur ultérieure
de publication vers Pushgateway peut donc faire échouer le flow alors que le
modèle a déjà été enregistré et éventuellement promu.

### Prédiction et mise à jour du modèle en mémoire

```mermaid
sequenceDiagram
    actor U as Utilisateur connecté
    participant S as Streamlit
    participant A as Prediction API
    participant M as MLflow
    participant O as MinIO
    Note over A: Au démarrage puis toutes les 60 secondes
    A->>M: Résoudre NYC_Taxi_Fare_Model@champion
    alt Une nouvelle version est disponible
        A->>M: Télécharger le modèle
        M->>O: Lire les artefacts S3
        O-->>M: Artefacts
        M-->>A: Modèle à charger en mémoire
    end
    U->>S: Renseigner le trajet et cliquer sur Predict Fare
    S->>A: POST /fare_predict
    alt Requête invalide
        A-->>S: 422 — validation du schéma
    else Aucun modèle en mémoire
        A->>M: Essayer de charger le champion immédiatement
        Note over A,M: 503 si le chargement reste impossible
    end
    Note over A: Avec un modèle disponible et une requête valide
    A->>A: Construire les 9 features et appeler model.predict
    A-->>S: predicted_fare
    S-->>U: Afficher le prix estimé
```

La prédiction ne lit pas PostgreSQL et ne lance aucun flow Prefect. Le modèle
chargé reste en mémoire entre les requêtes. Si une vérification périodique échoue,
un modèle déjà chargé reste utilisable. Les erreurs d'inférence retournent 500.

### Rapports Evidently : stockage et consultation

```mermaid
sequenceDiagram
    participant F as Conteneur du flow
    participant M as MLflow
    participant O as MinIO
    actor U as Utilisateur connecté
    participant S as Streamlit
    F->>M: Enregistrer les artefacts du run
    M->>O: Écrire modèle, référence et résumé de dérive
    opt Une référence précédente existe
        M->>O: Écrire drift/drift.html et drift/drift.json
    end
    U->>S: Ouvrir Rapports Evidently et choisir un run
    S->>M: Lister les derniers runs et charger le résumé
    M->>O: Lire drift/summary.json
    O-->>M: Résumé
    M-->>S: Résumé
    alt Run terminé avec rapport comparatif
        S->>M: Charger drift/drift.html
        M->>O: Lire le rapport HTML
        O-->>M: HTML
        M-->>S: HTML
        S-->>U: Affichage intégré et téléchargement
    else Première baseline ou run non terminé
        S-->>U: Message expliquant l'absence de rapport
    end
```

Le rapport ne dépend pas de la survie du conteneur d'entraînement. Streamlit le
récupère via MLflow, qui accède au bucket privé MinIO. Le portail affiche les
30 derniers runs de l'expérience.

### Observabilité : trois circuits complémentaires

```mermaid
flowchart TB
    API[Prediction API] -->|Expose /metrics| Prom[Prometheus]
    Flow[Conteneur du flow] -->|Pousse scores de dérive, RMSE et nombre de lignes| Push[Pushgateway]
    Push -->|Expose les dernières valeurs| Prom
    Prom --> Rules[Évaluer les règles d'alerte]
    Containers[Logs des conteneurs de la stack] --> Tail[Promtail]
    Tail --> Loki[Loki]
    API -->|Spans OpenTelemetry via OTLP| Tempo[Tempo]
    Flow -.->|États et logs des tâches| Prefect[Prefect Server / UI]
    Grafana[Grafana] -->|Interroge les métriques| Prom
    Grafana -->|Interroge les logs| Loki
    Grafana -->|Interroge les traces| Tempo
    Browser[Navigateur dans le portail Streamlit] -->|Iframe| Grafana
    Browser -->|Iframe| Prefect
```

Prometheus collecte toutes les cinq secondes. Pushgateway conserve le dernier
résultat publié après la disparition du flow : ces valeurs ne représentent pas
un traitement en cours. Prefect reste la source pour le statut des exécutions.
Les règles Prometheus signalent les alertes ; aucun Alertmanager ni envoi de
notification externe n'est configuré. La collecte des traces est actuellement
instrumentée dans la Prediction API.

## Configuration et accès

Les variables `*_UI_URL` sont des adresses accessibles depuis le navigateur.
Pour une présentation depuis un autre ordinateur, remplacer `localhost` par le
nom de l'hôte dans ces variables et dans `PREFECT_BROWSER_API_URL`. Conserver le
même nom d'hôte pour Streamlit et les interfaces embarquées.

L'authentification Streamlit protège le portail uniquement, pas les ports directs.
Grafana autorise les iframes et l'accès anonyme Viewer pour cette démonstration.
MinIO utilise `minioadmin` / `minioadmin` et Grafana `admin` / `admin`. Cette
configuration est destinée à une démo locale et ne constitue pas un SSO.

## Développement et validation avec uv

Les dépendances sont déclarées par groupe dans `pyproject.toml` et figées dans
`uv.lock`. Toutes les images Python utilisent `uv sync --frozen`.

```bash
uv sync --frozen --group pipeline --group frontend --group api --group dev
uv run --no-sync pytest -q
PORTAL_PASSWORD=validation-only docker compose config --quiet
# Après le démarrage, vérification réelle MLflow / MinIO / prédiction :
uv run --no-sync python scripts/verify_demo.py
# Vérifier accès anonyme, requêtes des dashboards, logs et traces :
uv run --no-sync python scripts/verify_monitoring.py
```

Le script de vérification attend le premier modèle, contrôle sa présence dans
MinIO, demande une prédiction, puis lance un deuxième flow pour vérifier le
rapport Evidently. Il nécessite la démo en cours sur les ports par défaut.
