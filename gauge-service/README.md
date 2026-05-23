# Gauge service

Service Flask séparé du `web-server/server.py` historique.

## Fonctions

- réception des images en `POST /upload`
- renommage par timestamp de réception
- conservation paramétrable des photos et des métadonnées extraites
- lecture de jauge via calibration manuelle (page `/setup`)
- compensation de dérive caméra par recalage sur des zones de référence
- publication MQTT + autodiscovery Home Assistant au démarrage
- **page de configuration du device ESP32** (`/device`) — pousse les paramètres en MQTT avec retain
- **gestion OTA firmware** — 4 modes : Disabled / GitHub auto / Service auto / Manuel
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
- `OTA_MODE` — mode OTA : `disabled` (défaut) | `github_auto` | `service_auto` | `manual`
- `GITHUB_REPO` — dépôt GitHub au format `owner/repo` (ex. `remiserriere/ESP32-S3-Camera-EnvSensors`)

### Calibration

La calibration se fait via l'interface web à `/setup` :

1. Définir ≥ 2 zones de référence (drag sur l'image) pour la compensation de dérive
2. Cliquer ≥ 3 points sur le périmètre du cadran pour ajuster le cercle
3. Cliquer chaque graduation et saisir sa valeur
4. Simuler une lecture et copier le snippet `GAUGE_CONFIG` dans le YAML de déploiement

### Configuration du device ESP32 (`/device`)

La page `/device` permet de configurer le device ESP32 à distance via MQTT.

**Comment ça marche :**

1. Remplir les paramètres (capteurs, schedule, Wi-Fi, MQTT, NTP, OTA…)
2. Cliquer **Save & Push to device**
3. Le service publie un message JSON en **retain** sur le topic `<mqtt_id>/config/set`
4. Au prochain réveil Wi-Fi, le device lira le message retenu et appliquera la configuration

Les paramètres sont stockés localement dans `DATA_DIR/device_config.json`.

**API REST :**

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/device-config` | Retourne la config device stockée |
| `POST` | `/api/device-config` | Sauvegarde + (optionnel) publie en MQTT |
| `POST` | `/api/device-config/publish` | Publie la config stockée en MQTT retain |

### Gestion OTA firmware

La page `/device` (onglet **OTA updates**) expose 4 modes configurables depuis `/config` (service settings) ou l'onglet OTA :

| Mode | Description |
|------|-------------|
| `disabled` | OTA désactivé (défaut) — `/api/ota/manifest` retourne 404 |
| `github_auto` | Le manifest est généré à la volée depuis l'API GitHub Releases. **Le device télécharge directement depuis GitHub.** |
| `service_auto` | Le service télécharge le .bin depuis GitHub et le met en cache localement. **Le device télécharge depuis ce service.** |
| `manual` | L'utilisateur uploade un .bin spécifique. Le service le sert au device. |

**URL à configurer sur le device (`OTA_MANIFEST_URL` / champ `ota_url` via MQTT) :**

```
http://<adresse-du-service>:8081/api/ota/manifest
```

**API OTA :**

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/ota/manifest` | Manifest JSON que l'ESP32 interroge (format `{version, url, notes}`) |
| `GET` | `/api/ota/status` | État OTA : mode, version en cache, tailles |
| `GET` | `/api/ota/config` | Configuration OTA actuelle |
| `POST` | `/api/ota/config` | Sauvegarde la config OTA (`mode`, `github_repo`, `manual_version`…) |
| `POST` | `/api/ota/fetch` | (mode `service_auto`) Télécharge le dernier firmware depuis GitHub |
| `POST` | `/api/ota/upload` | (mode `manual`) Upload d'un .bin + version string |
| `GET` | `/api/ota/firmware` | Sert le binaire en cache (modes `service_auto` et `manual`) |

**Note sur le mode manuel et la vérification de version :**

L'ESP32 compare la version du manifest avec sa propre `FIRMWARE_VERSION` via `isNewer()`. Si les deux chaînes ne parsent pas en semver, une simple inégalité de chaînes est utilisée. Conséquences :
- Pour mettre à jour, la version du manifest doit être **différente** de celle du device
- Pour forcer un re-flash quelle que soit la version courante, utiliser `99.99.99`

### Mode upload debug

`UPLOAD_DEBUG_MODE` contrôle la réponse de `POST /upload` :

- `false` (défaut): retourne `200` immédiatement (`status=accepted`) puis lance l'analyse en post-traitement.
- `true`: exécute l'analyse en synchrone et retourne le record complet avec `percentage`, `confidence`, `needle_angle`, `drift`, `source`, `estimated`.

## Image Docker

L'image est publiée automatiquement sur [GitHub Container Registry (GHCR)](https://ghcr.io) :

```bash
# Dernière version stable (depuis main)
docker pull ghcr.io/remiserriere/gauge-service:latest

# Version spécifique (tag Git)
docker pull ghcr.io/remiserriere/gauge-service:v1.2.3
```

### Exemple docker-compose

```yaml
services:
  gauge-service:
    image: ghcr.io/remiserriere/gauge-service:latest
    ports:
      - "8081:8081"
    volumes:
      - ./data:/data
    environment:
      - DATA_DIR=/data
      - MAX_SNAPSHOTS=100
      - MQTT_HOST=192.168.1.10
      - MQTT_PORT=1883
      - DEVICE_NAME=gauge
      - GAUGE_CONFIG={}        # remplacer par le JSON généré via /setup
      - UPLOAD_DEBUG_MODE=false
      - OTA_MODE=disabled      # disabled | github_auto | service_auto | manual
      - GITHUB_REPO=remiserriere/ESP32-S3-Camera-EnvSensors
    restart: unless-stopped
```

## CI / CD

Trois workflows GitHub Actions sont en place :

| Workflow | Fichier | Déclencheur | Ce qu'il fait |
|---|---|---|---|
| **CI – Gauge service** | `.github/workflows/gauge-service.yml` | Push / PR sur `gauge-service/**` | Tests pytest + push image `latest` sur GHCR (branche main uniquement) |
| **Release** | `.github/workflows/release.yml` | Push d'un tag `v*` | Build firmware ESP32 + GitHub Release + push image Docker versionnée sur GHCR |
| **CI – Build Firmware** | `.github/workflows/ci.yml` | Push / PR sur `src/**`, `platformio.ini`… | Build firmware uniquement (ne se déclenche PAS sur les modifs gauge-service) |

### Tests

```bash
cd gauge-service
pip install -r requirements.txt
PYTHONPATH=gauge-service pytest gauge-service/tests -q
```

