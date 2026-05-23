# Gauge service

Service Flask séparé du `web-server/server.py` historique.

## Fonctions

- réception des images en `POST /upload`
- renommage par timestamp de réception
- conservation paramétrable des photos et des métadonnées extraites
- lecture de jauge sans apprentissage local, via OpenCV
- publication MQTT + autodiscovery Home Assistant au démarrage
- interface web minimale sur `/`

## Choix techniques

Le service utilise **OpenCV** plutôt que TensorFlow :

- détection du cadran par géométrie
- détection de l’aiguille en priorité via les segments rouges
- tentative de repérage des extrémités de l’échelle (`5` / `95`) par vision classique
- repli configurable par angles par défaut si l’image est partielle ou si la détection est incomplète

Dans ce cas, le champ `estimated` passe à `true` et une `confidence` plus faible est publiée.

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

- `MAX_SNAPSHOTS`
- `DATA_DIR`
- `MQTT_*`
- `DEVICE_NAME`
- `ANALYSIS_*`

### Miroir caméra

Pour une image retournée en miroir par le device, régler `ANALYSIS_MIRROR_MODE` :

- `none` (défaut)
- `horizontal`
- `vertical`
- `both`

### Mode upload debug

`UPLOAD_DEBUG_MODE` contrôle la réponse de `POST /upload` :

- `false` (défaut): retourne `200` immédiatement (`status=accepted`) puis lance l’analyse en post-traitement.
- `true`: exécute l’analyse en synchrone et retourne le record complet avec détails OCR dans `analysis.source.ocr_labels`.
