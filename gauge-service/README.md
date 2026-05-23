# Gauge service

Service Flask séparé du `web-server/server.py` historique.

## Fonctions

- réception des images en `POST /upload`
- renommage par timestamp de réception
- conservation paramétrable des photos et des métadonnées extraites
- lecture de jauge via calibration manuelle (page `/setup`)
- compensation de dérive caméra par recalage sur des zones de référence
- publication MQTT + autodiscovery Home Assistant au démarrage
- interface web minimale sur `/`

## Choix techniques

La lecture utilise **OpenCV** pour :

- compensation de dérive caméra par template matching sur les zones de référence définies lors du setup
- détection de l'aiguille via la couleur HSV (méthode primaire) ou sweep radial de contraste (fallback)
- interpolation linéaire entre les repères définis manuellement

Le champ `estimated` passe à `true` et une `confidence` plus faible est publiée lorsque la dérive est trop importante ou que la détection est ambiguë.

## Lancement local

```bash
cd gauge-service
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m gauge_service.app
```

## Variables principales

Toutes les variables sont prévues pour être fixées directement dans `docker-compose.yml` ou `k8s/gauge-service.yaml`.

- `MAX_SNAPSHOTS` — nombre de snapshots conservés (0 = illimité)
- `DATA_DIR`
- `MQTT_*`
- `DEVICE_NAME`
- `GAUGE_CONFIG` — JSON généré par l'assistant de calibration (`/setup`), vide = stockage sans lecture
- `MAX_DELTA_PERCENT` — écart maximal autorisé entre deux lectures consécutives (0 = désactivé)

### Calibration

La calibration se fait via l'interface web à `/setup` :

1. Définir ≥ 2 zones de référence (drag sur l'image) pour la compensation de dérive
2. Cliquer ≥ 3 points sur le périmètre du cadran pour ajuster le cercle
3. Cliquer chaque graduation et saisir sa valeur
4. Simuler une lecture et copier le snippet `GAUGE_CONFIG` dans le YAML de déploiement

### Mode upload debug

`UPLOAD_DEBUG_MODE` contrôle la réponse de `POST /upload` :

- `false` (défaut): retourne `200` immédiatement (`status=accepted`) puis lance l'analyse en post-traitement.
- `true`: exécute l'analyse en synchrone et retourne le record complet avec `percentage`, `confidence`, `needle_angle`, `drift`, `source`, `estimated`.
