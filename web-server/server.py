from flask import Flask, request, jsonify, send_from_directory, abort
import os, json, time

app = Flask(__name__)

PHOTO_DIR    = "photos"
FIRMWARE_DIR = "firmware"
os.makedirs(PHOTO_DIR,    exist_ok=True)
os.makedirs(FIRMWARE_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Photo upload
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload():
    meta = json.loads(request.form.get("metadata", "{}"))
    img  = request.files.get("image")
    if img:
        ts   = meta.get("timestamp", int(time.time()))
        name = f"{meta.get('device_id', 'esp32')}_{ts}.jpg"
        img.save(os.path.join(PHOTO_DIR, name))
        print(f"[PHOTO] Saved {name}")
    return "", 200

# ─────────────────────────────────────────────────────────────────────────────
#  OTA firmware distribution
#
#  Directory layout expected:
#    firmware/
#      manifest.json          ← version descriptor
#      firmware_v1.2.3.bin    ← binary pointed to by manifest "url" field
#
#  manifest.json example:
#    {
#      "version": "v1.2.3",
#      "url":     "http://192.168.1.100:8080/firmware/firmware_v1.2.3.bin",
#      "notes":   "Bug fixes"
#    }
#
#  For HTTPS: run with ssl_context=('cert.pem', 'key.pem') and update the
#  "url" field in manifest.json to use https://.
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/firmware/manifest.json")
def firmware_manifest():
    path = os.path.join(FIRMWARE_DIR, "manifest.json")
    if not os.path.isfile(path):
        abort(404, description="manifest.json not found – create firmware/manifest.json first")
    with open(path) as f:
        data = json.load(f)
    print(f"[OTA] Manifest served: version={data.get('version', '?')}")
    return jsonify(data)

@app.route("/firmware/<path:filename>")
def firmware_file(filename):
    # Restrict to .bin files to avoid accidentally serving arbitrary files.
    if not filename.endswith(".bin"):
        abort(403, description="Only .bin files are served from this endpoint")
    full = os.path.join(FIRMWARE_DIR, filename)
    if not os.path.isfile(full):
        abort(404, description=f"{filename} not found in firmware/")
    size = os.path.getsize(full)
    print(f"[OTA] Serving {filename} ({size} bytes)")
    return send_from_directory(FIRMWARE_DIR, filename, mimetype="application/octet-stream")

# ─────────────────────────────────────────────────────────────────────────────

app.run(host="0.0.0.0", port=8080)
