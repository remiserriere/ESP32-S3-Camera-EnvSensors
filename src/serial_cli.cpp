#include "serial_cli.h"
#include "device_config.h"
#include "persistence.h"
#include "time_manager.h"
#include "sensors/ds18b20.h"
#include "sensors/sht3x.h"
#include "sensors/ina219.h"
#include "bthome/bthome.h"
#include "camera/camera_module.h"
#include "uploader/uploader.h"
#include <WiFiServer.h>
#include <WiFiClient.h>
#include "mqtt_config/mqtt_config.h"
#include "ota/ota.h"
#include "config.h"
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <string.h>
#include <vector>

// ─────────────────────────────────────────────────────────────────────────────
//  Low-level input helpers
// ─────────────────────────────────────────────────────────────────────────────

// Read a line from Serial, echoing characters. Returns on '\n' or timeout.
static String readLine(uint32_t timeoutMs = 60000) {
    String line;
    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        if (Serial.available()) {
            char c = (char)Serial.read();
            if (c == '\r') {
                // Consume optional trailing \n (\r\n sent by some terminals)
                delay(5);
                if (Serial.available() && Serial.peek() == '\n') Serial.read();
                break;
            }
            if (c == '\n') break;
            if (c == '\b' || c == 127) {   // backspace / DEL
                if (line.length() > 0) {
                    line.remove(line.length() - 1);
                    Serial.print(F("\b \b"));
                }
                continue;
            }
            line += c;
            Serial.print(c);   // echo
        }
        delay(5);
    }
    Serial.println();
    return line;
}

// Like readLine but does not echo (for passwords).
static String readLineHidden(uint32_t timeoutMs = 60000) {
    String line;
    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        if (Serial.available()) {
            char c = (char)Serial.read();
            if (c == '\r') {
                // Consume optional trailing \n (\r\n sent by some terminals)
                delay(5);
                if (Serial.available() && Serial.peek() == '\n') Serial.read();
                break;
            }
            if (c == '\n') break;
            if (c == '\b' || c == 127) {
                if (line.length() > 0) line.remove(line.length() - 1);
                continue;
            }
            line += c;
        }
        delay(5);
    }
    Serial.println(F("(hidden)"));
    return line;
}

// ─────────────────────────────────────────────────────────────────────────────
//  Web config helpers (boot window HTTP server)
// ─────────────────────────────────────────────────────────────────────────────

// Percent-decode a URL-encoded string (+ → space, %XX → char).
static String urlDecode(const String& s) {
    String out;
    out.reserve(s.length());
    for (int i = 0; i < (int)s.length(); i++) {
        if (s[i] == '+') {
            out += ' ';
        } else if (s[i] == '%' && i + 2 < (int)s.length()) {
            char hex[3] = { s[i + 1], s[i + 2], '\0' };
            out += (char)strtol(hex, nullptr, 16);
            i += 2;
        } else {
            out += s[i];
        }
    }
    return out;
}

// Apply one decoded key=value pair from the web form to g_deviceConfig.
// Called left-to-right so later values win (handles hidden+checkbox pattern).
static void applyFormField(const String& key, const String& val) {
    if      (key == "ds18_en")   g_deviceConfig.ds18b20Enabled     = (val == "1");
    else if (key == "ds18_int")  g_deviceConfig.ds18b20IntervalMin = (uint8_t)constrain(val.toInt(), 1, 240);
    else if (key == "sht_en")    g_deviceConfig.sht3xEnabled       = (val == "1");
    else if (key == "sht_int")   g_deviceConfig.sht3xIntervalMin   = (uint8_t)constrain(val.toInt(), 1, 240);
    else if (key == "ina_en")    g_deviceConfig.ina219Enabled      = (val == "1");
    else if (key == "ina_int")   g_deviceConfig.ina219IntervalMin  = (uint8_t)constrain(val.toInt(), 1, 240);
    else if (key == "ph_hour")   g_deviceConfig.photoHour          = (uint8_t)constrain(val.toInt(), 0, 23);
    else if (key == "ph_min")    g_deviceConfig.photoMinute        = (uint8_t)constrain(val.toInt(), 0, 59);
    else if (key == "ph_win")    g_deviceConfig.photoWindowMin     = (uint8_t)constrain(val.toInt(), 1, 60);
    else if (key == "cb_photo_en") g_deviceConfig.coldBootPhotoEn   = (val == "1");
    else if (key == "ota_en")    g_deviceConfig.otaEnabled         = (val == "1");
    else if (key == "mqtt_en")   g_deviceConfig.mqttEnabled        = (val == "1");
    else if (key == "mqtt_port") { int v = val.toInt(); if (v >= 1 && v <= 65535) g_deviceConfig.mqttPort = (uint16_t)v; }
    else if (key == "boot_win")  g_deviceConfig.bootWindowSec      = (uint8_t)constrain(val.toInt(), 0, 180);
    else if (key == "diag_en")   g_deviceConfig.diagEn             = (val == "1");
    // String fields: only update when the user actually typed something.
    else if (!val.isEmpty()) {
        if      (key == "wifi_ssid")  strncpy(g_deviceConfig.wifiSsid,       val.c_str(), sizeof(g_deviceConfig.wifiSsid)       - 1);
        else if (key == "wifi_pass")  strncpy(g_deviceConfig.wifiPassword,   val.c_str(), sizeof(g_deviceConfig.wifiPassword)   - 1);
        else if (key == "upload_ep")  strncpy(g_deviceConfig.uploadEndpoint, val.c_str(), sizeof(g_deviceConfig.uploadEndpoint) - 1);
        else if (key == "ota_url")    strncpy(g_deviceConfig.otaManifestUrl, val.c_str(), sizeof(g_deviceConfig.otaManifestUrl) - 1);
        else if (key == "mqtt_host")  strncpy(g_deviceConfig.mqttBroker,     val.c_str(), sizeof(g_deviceConfig.mqttBroker)     - 1);
        else if (key == "mqtt_user")  strncpy(g_deviceConfig.mqttUser,       val.c_str(), sizeof(g_deviceConfig.mqttUser)       - 1);
        else if (key == "mqtt_pass")  strncpy(g_deviceConfig.mqttPassword,   val.c_str(), sizeof(g_deviceConfig.mqttPassword)   - 1);
        else if (key == "mqtt_id")    strncpy(g_deviceConfig.mqttClientId,   val.c_str(), sizeof(g_deviceConfig.mqttClientId)   - 1);
        else if (key == "dev_name")   strncpy(g_deviceConfig.deviceName,     val.c_str(), sizeof(g_deviceConfig.deviceName)     - 1);
        else if (key == "ntp_srv1")  strncpy(g_deviceConfig.ntpServer1,  val.c_str(), sizeof(g_deviceConfig.ntpServer1)  - 1);
        else if (key == "ntp_srv2")  strncpy(g_deviceConfig.ntpServer2,  val.c_str(), sizeof(g_deviceConfig.ntpServer2)  - 1);
        else if (key == "ntp_tz")    strncpy(g_deviceConfig.ntpTimezone, val.c_str(), sizeof(g_deviceConfig.ntpTimezone) - 1);
    }
    // "action" is handled by the caller; ignored here.
}

// Parse an application/x-www-form-urlencoded body and apply every field.
static void parseAndApplyFormBody(const String& body) {
    int start = 0;
    while (start < (int)body.length()) {
        int amp  = body.indexOf('&', start);
        if (amp < 0) amp = body.length();
        String pair = body.substring(start, amp);
        int eq = pair.indexOf('=');
        if (eq > 0) {
            String key = urlDecode(pair.substring(0, eq));
            String val = urlDecode(pair.substring(eq + 1));
            applyFormField(key, val);
        }
        start = amp + 1;
    }
}

// Send the full HTML configuration page to the connected client.
static void sendConfigPage(WiFiClient& client, int secondsLeft) {
    client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"));

    // ── Head + CSS ──────────────────────────────────────────────────────────
    client.print(F(
        "<!DOCTYPE html><html lang='fr'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>ESP32-S3 Config</title>"
        "<style>"
        "body{font-family:sans-serif;max-width:680px;margin:16px auto;padding:0 12px;background:#f5f5f5}"
        "h1{font-size:1.25em;border-bottom:2px solid #1565c0;padding-bottom:6px;color:#1565c0}"
        "h3{font-size:.95em;background:#e3f2fd;padding:5px 10px;border-radius:4px;margin:16px 0 8px}"
        "label{display:block;font-size:.82em;font-weight:600;margin:7px 0 2px;color:#333}"
        "input[type=text],input[type=password],input[type=number],input[type=url]"
        "{width:100%;box-sizing:border-box;padding:5px;border:1px solid #bbb;border-radius:4px;font-size:.88em}"
        ".cb{display:flex;align-items:center;gap:8px;margin:7px 0}"
        ".cb label{margin:0}"
        ".cb input[type=checkbox]{width:auto}"
        ".actions{display:flex;gap:8px;margin-top:18px;flex-wrap:wrap;align-items:center}"
        ".btn{padding:9px 14px;font-size:.88em;border:none;border-radius:5px;cursor:pointer;color:#fff;white-space:nowrap}"
        ".bc{background:#1976d2}.bsave{background:#2e7d32}.bdiscard{background:#616161}"
        ".bdef{background:#e65100}.bpause{background:#6a1b9a}"
        ".info{font-size:.82em;color:#555;margin:4px 0 10px}"
        ".cd{color:#c62828;font-weight:700}"
        ".paused{color:#6a1b9a;font-weight:700}"
        ".diag-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:8px 0}"
        ".diag-btn{padding:7px 10px;font-size:.83em;border:none;border-radius:4px;cursor:pointer;"
        "background:#37474f;color:#fff;text-align:left}"
        ".diag-btn:hover{background:#546e7a}"
        ".diag-out{font-family:monospace;font-size:.8em;background:#263238;color:#b2dfdb;"
        "padding:8px;border-radius:4px;white-space:pre-wrap;max-height:160px;overflow-y:auto;"
        "margin-top:6px;display:none}"
        "</style>"
        "<script>"
        "var R="));
    client.print(secondsLeft);
    client.print(F(
        ",paused=(R<0);"
        "if(paused)R=60;"                      // R used for resume value
        "function tick(){"
        "if(paused)return;"
        "var e=document.getElementById('cd');"
        "if(e)e.textContent=R;"
        "if(--R<0){submitAction('timeout');}else setTimeout(tick,1000);}"
        "function pauseTimer(){"
        "paused=!paused;"
        "var b=document.getElementById('pbtn');"
        "var e=document.getElementById('cd');"
        "if(paused){"
          "fetch('/pause');"
          "b.textContent='\u25b6 Reprendre';"
          "if(e){e.className='paused';e.textContent='en pause';}"
        "}else{"
          "fetch('/resume').then(function(r){return r.text();}).then(function(s){R=parseInt(s)||60;if(e){e.className='cd';e.textContent=R;}tick();});"
          "b.textContent='\u23f8 Pause';"
        "}"
        "}"
        "function submitAction(a){"
        "var f=document.getElementById('fc');"
        "var i=document.createElement('input');"
        "i.type='hidden';i.name='action';i.value=a;"
        "f.appendChild(i);f.submit();"
        "}"
        "function runDiag(cmd){"
        "var out=document.getElementById('diag-out');"
        "out.style.display='block';"
        "out.textContent='En cours\u2026';"
        "var xhr=new XMLHttpRequest();"
        "xhr.open('GET','/diag?cmd='+cmd);"
        "xhr.timeout=30000;"
        "xhr.onload=function(){out.textContent=xhr.responseText;out.scrollTop=out.scrollHeight;};"
        "xhr.onerror=function(){out.textContent='Erreur r\u00e9seau.';};"
        "xhr.ontimeout=function(){out.textContent='Timeout.';};"
        "xhr.send();"
        "}"
        "window.onload=function(){"
        "if(paused){"
          "var b=document.getElementById('pbtn');"
          "var e=document.getElementById('cd');"
          "if(b)b.textContent='\u25b6 Reprendre';"
          "if(e){e.className='paused';e.textContent='en pause';}"
        "}else{tick();}"
        "};"
        "</script></head><body>"
        "<h1>&#9881; ESP32-S3 &#8212; Configuration de d&#233;marrage</h1>"
        "<p class='info'>Boot automatique dans <span id='cd' class='cd'>"));
    if (secondsLeft < 0) {
        client.print(F("<span class='paused'>en pause</span>"));
    } else {
        client.print(secondsLeft);
    }
    client.print(F(
        "</span> s. Modifiez les param&#232;tres puis cliquez sur un bouton.</p>"
        "<form id='fc' method='POST' action='/save'>"));

    // ── Système ─────────────────────────────────────────────────────────────
    client.print(F("<h3>&#9881; Syst&#232;me</h3>"));
    client.printf(
        "<label>Nom de l'appareil</label>"
        "<input type='text' name='dev_name' value='%s' maxlength='31'>\n",
        g_deviceConfig.deviceName);
    client.printf(
        "<label>Fen&#234;tre de d&#233;marrage (0&#8209;180&nbsp;s &mdash; 0&nbsp;=&nbsp;web d&#233;sactiv&#233;)</label>"
        "<input type='number' name='boot_win' min='0' max='180' value='%u'>\n",
        g_deviceConfig.bootWindowSec);
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='diag_en' value='0'>"
        "<input type='checkbox' name='diag_en' value='1'%s>"
        "<label>Diagnostics BLE (compteurs next wakeup/photo dans le scan response)</label></div>\n",
        g_deviceConfig.diagEn ? " checked" : "");

    // ── Réseau ──────────────────────────────────────────────────────────────
    client.print(F("<h3>&#128246; R&#233;seau Wi&#8209;Fi</h3>"));
    client.printf(
        "<label>SSID</label>"
        "<input type='text' name='wifi_ssid' value='%s' maxlength='63'>\n",
        g_deviceConfig.wifiSsid);
    client.print(F(
        "<label>Mot de passe (vide&nbsp;= conserver l'actuel)</label>"
        "<input type='password' name='wifi_pass' placeholder='&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;'>\n"));
    client.printf(
        "<label>Endpoint upload</label>"
        "<input type='url' name='upload_ep' value='%s' maxlength='127'>\n",
        g_deviceConfig.uploadEndpoint);

    // ── NTP & Fuseau horaire ─────────────────────────────────────────────────
    client.print(F("<h3>&#128336; NTP &amp; Fuseau horaire</h3>"));
    client.printf(
        "<label>Serveur NTP 1</label>"
        "<input type='text' name='ntp_srv1' value='%s' maxlength='63'>\n",
        g_deviceConfig.ntpServer1);
    client.printf(
        "<label>Serveur NTP 2</label>"
        "<input type='text' name='ntp_srv2' value='%s' maxlength='63'>\n",
        g_deviceConfig.ntpServer2);
    client.printf(
        "<label>Fuseau horaire (POSIX TZ, ex.&nbsp;CET-1CEST,M3.5.0,M10.5.0/3)</label>"
        "<input type='text' name='ntp_tz' value='%s' maxlength='63'>\n",
        g_deviceConfig.ntpTimezone);

    // ── Capteurs ────────────────────────────────────────────────────────────
    client.print(F("<h3>&#127777; Capteurs</h3>"));
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='ds18_en' value='0'>"
        "<input type='checkbox' name='ds18_en' value='1'%s>"
        "<label>DS18B20 activ&#233;</label></div>\n"
        "<label>Intervalle DS18B20 (min)</label>"
        "<input type='number' name='ds18_int' min='1' max='240' value='%u'>\n",
        g_deviceConfig.ds18b20Enabled ? " checked" : "",
        g_deviceConfig.ds18b20IntervalMin);
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='sht_en' value='0'>"
        "<input type='checkbox' name='sht_en' value='1'%s>"
        "<label>SHT3x activ&#233;</label></div>\n"
        "<label>Intervalle SHT3x (min)</label>"
        "<input type='number' name='sht_int' min='1' max='240' value='%u'>\n",
        g_deviceConfig.sht3xEnabled ? " checked" : "",
        g_deviceConfig.sht3xIntervalMin);
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='ina_en' value='0'>"
        "<input type='checkbox' name='ina_en' value='1'%s>"
        "<label>INA219 activ&#233;</label></div>\n"
        "<label>Intervalle INA219 (min)</label>"
        "<input type='number' name='ina_int' min='1' max='240' value='%u'>\n",
        g_deviceConfig.ina219Enabled ? " checked" : "",
        g_deviceConfig.ina219IntervalMin);

    // ── Photo ────────────────────────────────────────────────────────────────
    client.print(F("<h3>&#128247; Planification photo</h3>"));
    client.printf(
        "<label>Heure (0&#8209;23)</label>"
        "<input type='number' name='ph_hour' min='0' max='23' value='%u'>\n"
        "<label>Minute (0&#8209;59)</label>"
        "<input type='number' name='ph_min' min='0' max='59' value='%u'>\n"
        "<label>Fen&#234;tre d'acceptation (min)</label>"
        "<input type='number' name='ph_win' min='1' max='60' value='%u'>\n",
        g_deviceConfig.photoHour, g_deviceConfig.photoMinute, g_deviceConfig.photoWindowMin);
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='cb_photo_en' value='0'>"
        "<input type='checkbox' name='cb_photo_en' value='1'%s>"
        "<label>Photo au d&#233;marrage &#224; froid (cold-boot)</label></div>\n",
        g_deviceConfig.coldBootPhotoEn ? " checked" : "");

    // ── OTA ─────────────────────────────────────────────────────────────────
    client.print(F("<h3>&#128260; OTA</h3>"));
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='ota_en' value='0'>"
        "<input type='checkbox' name='ota_en' value='1'%s>"
        "<label>OTA activ&#233;</label></div>\n"
        "<label>URL manifest</label>"
        "<input type='url' name='ota_url' value='%s' maxlength='127'>\n",
        g_deviceConfig.otaEnabled ? " checked" : "",
        g_deviceConfig.otaManifestUrl);

    // ── MQTT ─────────────────────────────────────────────────────────────────
    client.print(F("<h3>&#128233; MQTT</h3>"));
    client.printf(
        "<div class='cb'>"
        "<input type='hidden' name='mqtt_en' value='0'>"
        "<input type='checkbox' name='mqtt_en' value='1'%s>"
        "<label>MQTT activ&#233;</label></div>\n"
        "<label>Broker (IP ou hostname)</label>"
        "<input type='text' name='mqtt_host' value='%s' maxlength='63'>\n"
        "<label>Port</label>"
        "<input type='number' name='mqtt_port' min='1' max='65535' value='%u'>\n"
        "<label>Utilisateur (vide&nbsp;= aucun)</label>"
        "<input type='text' name='mqtt_user' value='%s' maxlength='31'>\n",
        g_deviceConfig.mqttEnabled ? " checked" : "",
        g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort, g_deviceConfig.mqttUser);
    client.print(F(
        "<label>Mot de passe MQTT (vide&nbsp;= conserver)</label>"
        "<input type='password' name='mqtt_pass' placeholder='&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;'>\n"));
    client.printf(
        "<label>Client ID</label>"
        "<input type='text' name='mqtt_id' value='%s' maxlength='31'>\n",
        g_deviceConfig.mqttClientId);

    // ── Boutons principaux ────────────────────────────────────────────────────────
    client.print(F(
        "<div class='actions'>"
        "<button class='btn bsave' name='action' value='continue'>"
        "&#10003; Sauvegarder &amp; continuer</button>"
        "<button class='btn bsave' name='action' value='reboot'>"
        "&#8635; Sauvegarder &amp; red&#233;marrer</button>"
        "<button class='btn bdiscard' type='button' onclick=\"submitAction('discard')\">"
        "&#9654; Continuer sans sauvegarder</button>"
        "<button class='btn bdef' type='button' onclick=\"submitAction('defaults')\">"
        "&#8635; Restaurer les d&#233;fauts</button>"
        "<button class='btn bpause' id='pbtn' type='button' onclick='pauseTimer()'>"
        "&#9208; Pause</button>"
        "</div>"
        "</form>"));

    // ── Section Diagnostics (hors formulaire) ──────────────────────────────
    client.print(F(
        "<h3>&#128270; Diagnostics</h3>"
        "<p class='info'>Les diagnostics utilisent la configuration actuelle écran (non encore sauvegardée).</p>"
        "<div class='diag-grid'>"
        "<button class='diag-btn' onclick=\"runDiag('W')\">&#128246; Test Wi&#8209;Fi</button>"
        "<button class='diag-btn' onclick=\"runDiag('N')\">&#128336; Sync NTP</button>"
        "<button class='diag-btn' onclick=\"runDiag('S')\">&#127777; Capteurs (sans BTHome)</button>"
        "<button class='diag-btn' onclick=\"runDiag('B')\">&#128309; Capteurs + BTHome</button>"
        "<button class='diag-btn' onclick=\"runDiag('I')\">&#128269; D&#233;couverte DS18B20</button>"
        "<button class='diag-btn' onclick=\"runDiag('M')\">&#128233; MQTT ping alive</button>"
        "<button class='diag-btn' onclick=\"runDiag('C')\">&#128247; Photo + upload</button>"
        "<button class='diag-btn' onclick=\"window.open('/stream','_blank')\">&#128249; Webcam live</button>"
        "</div>"
        "<div class='diag-out' id='diag-out'></div>"
        "</body></html>"));
}

// Handle one pending web client (non-blocking – call in a loop).
// Returns: 0 = served GET page or diag, 1 = POST → continue boot, 2 = POST → reboot.
// deadline is passed by reference so /pause and /resume can extend it server-side.
// serverPaused tracks whether the user paused from the web UI (so page reloads show it).
static int handleBootWebClient(WiFiServer& server, int secondsLeft, uint32_t& deadline, bool& serverPaused) {
    WiFiClient client = server.accept();
    if (!client) return 0;

    String method, path;
    int    contentLength = 0;
    String line;
    bool   firstLine = true;
    uint32_t t0 = millis();

    while (millis() - t0 < 3000) {
        if (!client.available()) { delay(1); continue; }
        char c = (char)client.read();
        if (c == '\n') {
            line.trim();
            if (firstLine) {
                int sp1 = line.indexOf(' ');
                int sp2 = line.indexOf(' ', sp1 + 1);
                if (sp1 > 0 && sp2 > sp1) {
                    method = line.substring(0, sp1);
                    path   = line.substring(sp1 + 1, sp2);
                }
                firstLine = false;
            } else if (line.length() == 0) {
                break;  // blank line = end of headers
            } else {
                String ll = line; ll.toLowerCase();
                if (ll.startsWith("content-length:"))
                    contentLength = line.substring(15).toInt();
            }
            line = "";
        } else if (c != '\r') {
            line += c;
        }
    }

    // ── GET /pause – extend server deadline (browser paused the countdown) ──────
    if (method == "GET" && path == "/pause") {
        serverPaused = true;
        deadline = millis() + 3600000UL;  // 1 hour – effectively infinite
        client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\npaused"));
        client.flush(); client.stop(); return 0;
    }

    // ── GET /resume – shrink deadline back to 60 s ────────────────────────────
    if (method == "GET" && path == "/resume") {
        serverPaused = false;
        deadline = millis() + 60000UL;   // 60 s grace period after unpause
        client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n60"));
        client.flush(); client.stop(); return 0;
    }

    // ── GET /stream – MJPEG live stream (blocks until client disconnects) ──────
    if (method == "GET" && path.startsWith("/stream")) {
        if (!camera_module::begin()) {
            client.print(F("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n"));
            client.flush(); client.stop(); return 0;
        }
        client.print(F(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
            "Connection: keep-alive\r\n\r\n"));
        while (client.connected()) {
            CameraFrame frame = camera_module::capture();
            if (!frame.valid) { delay(50); continue; }
            client.printf(
                "--frame\r\n"
                "Content-Type: image/jpeg\r\n"
                "Content-Length: %zu\r\n\r\n", frame.len);
            client.write(frame.buf, frame.len);
            client.print(F("\r\n"));
            camera_module::releaseFrame(frame);
            delay(100); // ~10 fps
        }
        camera_module::end();
        client.flush(); client.stop(); return 0;
    }

    // ── GET /capture – single JPEG snapshot ──────────────────────────────
    if (method == "GET" && path.startsWith("/capture")) {
        if (!camera_module::begin()) {
            client.print(F("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n"));
            client.flush(); client.stop(); return 0;
        }
        CameraFrame frame = camera_module::capture();
        if (frame.valid) {
            client.printf(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: image/jpeg\r\n"
                "Content-Length: %zu\r\n"
                "Connection: close\r\n\r\n", frame.len);
            client.write(frame.buf, frame.len);
            camera_module::releaseFrame(frame);
        } else {
            client.print(F("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n"));
        }
        camera_module::end();
        client.flush(); client.stop(); return 0;
    }

    // ── GET /diag?cmd=X – run a diagnostic and return plain-text result ──────
    if (method == "GET" && path.startsWith("/diag")) {
        int qi = path.indexOf("cmd=");
        char cmd = (qi >= 0) ? (char)toupper((unsigned char)path[qi + 4]) : '?';

        // Capture Serial output of the diag function into a String by redirecting
        // through a small capture buffer via a String stream trick.
        // Simpler: just call the function and stream fixed status lines.
        // ── cmd='B' – BTHome requires WiFi off: send response, then toggle WiFi ───
        if (cmd == 'B') {
            Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ_HZ);
            BtHomePayload payload = {};
            bool anyRead = false;
            if (g_deviceConfig.ds18b20Enabled) {
                nvs::begin(); ds18b20::begin();
                for (auto& r : ds18b20::readAll())
                    if (r.valid) { payload.temperatures.push_back(r.temperatureC); anyRead = true; }
            }
            if (g_deviceConfig.sht3xEnabled && sht3x::begin()) {
                Sht3xReading r = sht3x::read();
                if (r.valid) {
                    payload.temperatures.push_back(r.temperatureC);
                    payload.humidity = r.humidityPct; payload.hasHumidity = true; anyRead = true;
                }
            }
            if (g_deviceConfig.ina219Enabled && ina219::begin()) {
                Ina219Reading r = ina219::read();
                if (r.valid) {
                    payload.voltage = r.busVoltageV; payload.hasVoltage = true;
                    payload.currentA = r.currentMa / 1000.0f; payload.hasCurrent = true;
                    payload.powerW = r.powerMw / 1000.0f; payload.hasPower = true; anyRead = true;
                }
            }
            client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n"));
            if (!anyRead) {
                client.print(F("Aucun capteur disponible.\n"));
            } else {
                client.print(F("BTHome: coupure Wi-Fi (~5 s), reconnexion auto...\n"));
            }
            client.flush(); client.stop();
            if (anyRead) {
                server.end();
                uploader::wifiDisconnect();
                bthome::begin(); bthome::advertise(payload); bthome::end();
                Serial.println(F("[BTHome] Diffusion OK – reconnexion Wi-Fi..."));
                uploader::wifiConnect();
                server.begin();
            }
            return 0;
        }

        // ── cmd='I' – DS18B20 discovery ──────────────────────────────────────────
        if (cmd == 'I') {
            nvs::begin();
            uint8_t found = ds18b20::discoverAndStore();
            client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n"));
            if (found == 0) {
                client.print(F("Aucun capteur DS18B20 detecte.\n"));
            } else {
                client.printf("%u capteur(s) detecte(s) et memorises:\n", found);
                const auto& addrs = ds18b20::storedAddresses();
                for (size_t i = 0; i < addrs.size(); i++) {
                    const auto& a = addrs[i];
                    client.printf("  [%zu] %02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X\n",
                                  i, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]);
                }
                client.print(F("(stockes en NVS - utilises au prochain boot)\n"));
            }
            client.flush(); client.stop();
            return 0;
        }

        client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n"));

        // We can't trivially capture Serial output; instead we call helpers that
        // write to both Serial and client.
        auto wl = [&](const String& s) { Serial.print(s); client.print(s); };
        auto wf = [&](const char* s)   { Serial.print(s); client.print(s); };

        // WiFi is always connected here (HTTP server runs on it).
        switch (cmd) {
            case 'W': {
                String msg = "IP=" + WiFi.localIP().toString()
                           + "  RSSI=" + String(WiFi.RSSI()) + " dBm\n";
                wl(msg);
                break;
            }
            case 'N': {
                nvs::begin();
                bool ok = time_manager::syncNtp();
                if (ok) {
                    struct tm now_tm; time_manager::nowLocal(now_tm);
                    char buf[32]; strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &now_tm);
                    client.printf("OK: %s\n", buf);
                    Serial.printf("[NTP] %s\n", buf);
                } else {
                    wf("ERREUR: sync NTP echouee.\n");
                }
                break;
            }
            case 'S': {
                wf("[Capteurs] Lecture...\n");
                Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ_HZ);
                if (g_deviceConfig.ds18b20Enabled) {
                    nvs::begin();
                    ds18b20::begin();
                    auto readings = ds18b20::readAll();
                    for (size_t i = 0; i < readings.size(); i++)
                        if (readings[i].valid)
                            client.printf("DS18B20[%zu][%02X%02X]: %.2f C\n", i,
                                          readings[i].address[6], readings[i].address[7],
                                          readings[i].temperatureC);
                    if (readings.empty()) wf("DS18B20: aucun detecte\n");
                } else { wf("DS18B20: desactive\n"); }
                if (g_deviceConfig.sht3xEnabled) {
                    if (sht3x::begin()) {
                        Sht3xReading r = sht3x::read();
                        if (r.valid) client.printf("SHT3x: %.2f C  %.1f %%HR\n", r.temperatureC, r.humidityPct);
                        else wf("SHT3x: lecture echouee\n");
                    } else { wf("SHT3x: non detecte\n"); }
                } else { wf("SHT3x: desactive\n"); }
                if (g_deviceConfig.ina219Enabled) {
                    if (ina219::begin()) {
                        Ina219Reading r = ina219::read();
                        if (r.valid) client.printf("INA219: %.3f V  %.1f mA\n", r.busVoltageV, r.currentMa);
                        else wf("INA219: lecture echouee\n");
                    } else { wf("INA219: non detecte\n"); }
                } else { wf("INA219: desactive\n"); }
                break;
            }
            case 'M': {
                if (!g_deviceConfig.mqttEnabled || g_deviceConfig.mqttBroker[0] == '\0') {
                    wf("MQTT non configure.\n"); break;
                }
                bool ok = mqtt_config::publishAlive();
                wf(ok ? "OK: ping MQTT envoye.\n" : "ERREUR: ping MQTT echoue.\n");
                break;
            }
            case 'C': {
                wf("[CAM] Init...\n");
                if (!camera_module::begin()) {
                    wf("ERREUR: init camera echouee.\n"); break;
                }
                CameraFrame frame = camera_module::capture();
                if (!frame.valid) {
                    wf("ERREUR: capture echouee.\n");
                    camera_module::end(); break;
                }
                client.printf("Capture OK (%zu octets)\n", frame.len);
                UploadMetadata meta = {};
                meta.deviceId   = g_deviceConfig.deviceName;
                meta.timestampS = time_manager::nowEpoch();
                int code = uploader::uploadPhoto(frame.buf, frame.len, meta);
                camera_module::releaseFrame(frame);
                camera_module::end();
                client.printf("Upload: HTTP %d\n", code);
                break;
            }
            default:
                wf("Commande inconnue.\n");
                break;
        }
        client.flush();
        client.stop();
        return 0;
    }

    // ── POST /save ────────────────────────────────────────────────────────────────────
    if (method == "POST") {
        String body;
        body.reserve(contentLength);
        uint32_t t1 = millis();
        while ((int)body.length() < contentLength && millis() - t1 < 3000) {
            if (client.available()) body += (char)client.read();
            else delay(1);
        }

        // Extract action= before applying fields (action key must be found first)
        String actionVal;
        int idx = body.indexOf("action=");
        if (idx >= 0) {
            int end = body.indexOf('&', idx + 7);
            actionVal = urlDecode(end < 0 ? body.substring(idx + 7)
                                          : body.substring(idx + 7, end));
        }

        auto sendSimplePage = [&](const String& msg) {
            client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"));
            client.print(F("<html><body style='font-family:sans-serif;max-width:500px;margin:40px auto'>"));
            client.print(msg);
            client.print(F("</body></html>"));
        };

        if (actionVal == "discard" || actionVal == "timeout") {
            sendSimplePage(F("<h2>&#9654; Boot en cours sans sauvegarde&#8230;</h2>"));
            client.flush(); client.stop();
            return 1;  // continue boot, no save
        }

        if (actionVal == "defaults") {
            device_config::resetToDefaults();
            device_config::save();
            // Reload page to show new values
            client.print(F(
                "HTTP/1.1 303 See Other\r\nLocation: /\r\nConnection: close\r\n\r\n"));
            client.flush(); client.stop();
            return 0;
        }

        // actions: continue, reboot
        parseAndApplyFormBody(body);
        device_config::save();

        if (actionVal == "reboot") {
            sendSimplePage(F("<h2>&#10003; Sauvegard&#233; &#8212; Red&#233;marrage&#8230;</h2>"));
            client.flush(); client.stop();
            return 2;
        } else {
            sendSimplePage(F("<h2>&#10003; Sauvegard&#233; &#8212; Boot en cours&#8230;</h2>"));
            client.flush(); client.stop();
            return 1;
        }
    }

    // ── GET / (config page) ─────────────────────────────────────────────────────
    // Pass -1 as secondsLeft when server-side paused so the page auto-starts paused.
    sendConfigPage(client, serverPaused ? -1 : secondsLeft);
    client.flush();
    client.stop();
    return 0;
}

// Prompt for a boolean; Enter keeps current value.
static bool promptBool(const char* label, bool current) {
    Serial.printf("  %s [%s] (y/n, Enter=keep): ", label, current ? "y" : "n");
    String s = readLine();
    s.trim(); s.toLowerCase();
    if (s == "y" || s == "yes" || s == "1") return true;
    if (s == "n" || s == "no"  || s == "0") return false;
    return current;
}

// Prompt for a uint8_t in [minV, maxV]; Enter keeps current value.
static uint8_t promptU8(const char* label, uint8_t current, uint8_t minV, uint8_t maxV) {
    Serial.printf("  %s [%u] (%u-%u, Enter=keep): ", label, current, minV, maxV);
    String s = readLine();
    s.trim();
    if (s.isEmpty()) return current;
    int v = s.toInt();
    if (v < (int)minV || v > (int)maxV) {
        Serial.printf("  ! Out of range – keeping %u\r\n", current);
        return current;
    }
    return (uint8_t)v;
}

// Prompt for a string; Enter keeps current value.
static void promptStr(const char* label, char* buf, size_t maxLen, bool hidden = false) {
    if (hidden) {
        Serial.printf("  %s [***] (Enter=keep): ", label);
        String s = readLineHidden();
        s.trim();
        if (!s.isEmpty()) {
            strncpy(buf, s.c_str(), maxLen - 1);
            buf[maxLen - 1] = '\0';
        }
    } else {
        Serial.printf("  %s [%s] (Enter=keep): ", label, buf);
        String s = readLine();
        s.trim();
        if (!s.isEmpty()) {
            strncpy(buf, s.c_str(), maxLen - 1);
            buf[maxLen - 1] = '\0';
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  Sub-menus
// ─────────────────────────────────────────────────────────────────────────────

static void menuSensors() {
    Serial.println(F("\n── Capteurs (présence & intervalles) ─────────────────────"));

    g_deviceConfig.ds18b20Enabled = promptBool("DS18B20 activé", g_deviceConfig.ds18b20Enabled);
    if (g_deviceConfig.ds18b20Enabled)
        g_deviceConfig.ds18b20IntervalMin = promptU8("  Intervalle DS18B20 (min)",
                                                     g_deviceConfig.ds18b20IntervalMin, 1, 240);

    g_deviceConfig.sht3xEnabled = promptBool("SHT3x activé", g_deviceConfig.sht3xEnabled);
    if (g_deviceConfig.sht3xEnabled)
        g_deviceConfig.sht3xIntervalMin = promptU8("  Intervalle SHT3x (min)",
                                                   g_deviceConfig.sht3xIntervalMin, 1, 240);

    g_deviceConfig.ina219Enabled = promptBool("INA219 activé", g_deviceConfig.ina219Enabled);
    if (g_deviceConfig.ina219Enabled)
        g_deviceConfig.ina219IntervalMin = promptU8("  Intervalle INA219 (min)",
                                                    g_deviceConfig.ina219IntervalMin, 1, 240);
}

static void menuPhoto() {
    Serial.println(F("\n── Planification photo ────────────────────────────────────"));
    g_deviceConfig.photoHour      = promptU8("Heure (0-23)",          g_deviceConfig.photoHour,      0,  23);
    g_deviceConfig.photoMinute    = promptU8("Minute (0-59)",         g_deviceConfig.photoMinute,    0,  59);
    g_deviceConfig.photoWindowMin = promptU8("Fenêtre d'acceptation (min)", g_deviceConfig.photoWindowMin, 1, 60);
    g_deviceConfig.coldBootPhotoEn = promptBool("Photo au démarrage à froid (cold-boot)", g_deviceConfig.coldBootPhotoEn);
}

static void menuNetwork() {
    Serial.println(F("\n── Réseau ─────────────────────────────────────────────────"));
    promptStr("SSID Wi-Fi",          g_deviceConfig.wifiSsid,       sizeof(g_deviceConfig.wifiSsid));
    promptStr("Mot de passe Wi-Fi",  g_deviceConfig.wifiPassword,   sizeof(g_deviceConfig.wifiPassword), true);
    promptStr("Endpoint upload",     g_deviceConfig.uploadEndpoint, sizeof(g_deviceConfig.uploadEndpoint));
}

static void menuOta() {
    Serial.println(F("\n── OTA ────────────────────────────────────────────────────"));
    g_deviceConfig.otaEnabled = promptBool("OTA activé", g_deviceConfig.otaEnabled);
    if (g_deviceConfig.otaEnabled)
        promptStr("URL manifest OTA", g_deviceConfig.otaManifestUrl, sizeof(g_deviceConfig.otaManifestUrl));
}

static void menuMqtt() {
    Serial.println(F("\n── MQTT (config à distance) ───────────────────────────────"));
    g_deviceConfig.mqttEnabled = promptBool("MQTT activé", g_deviceConfig.mqttEnabled);
    if (g_deviceConfig.mqttEnabled) {
        promptStr("Broker (IP ou hostname)",  g_deviceConfig.mqttBroker,    sizeof(g_deviceConfig.mqttBroker));

        // Port – special-case: it's uint16_t, not uint8_t
        Serial.printf("  Port MQTT [%u] (1-65535, Enter=keep): ", g_deviceConfig.mqttPort);
        String s = readLine(); s.trim();
        if (!s.isEmpty()) {
            int v = s.toInt();
            if (v >= 1 && v <= 65535) g_deviceConfig.mqttPort = (uint16_t)v;
            else Serial.printf("  ! Hors plage – port conservé (%u)\r\n", g_deviceConfig.mqttPort);
        }

        promptStr("Utilisateur MQTT (vide=aucun)", g_deviceConfig.mqttUser, sizeof(g_deviceConfig.mqttUser));
        promptStr("Mot de passe MQTT",             g_deviceConfig.mqttPassword, sizeof(g_deviceConfig.mqttPassword), true);
        promptStr("Client ID MQTT",                g_deviceConfig.mqttClientId, sizeof(g_deviceConfig.mqttClientId));
    }
}

static void menuBle() {
    Serial.println(F("\n── BLE ────────────────────────────────────────────────────"));
    promptStr("Nom BLE de l'appareil", g_deviceConfig.deviceName, sizeof(g_deviceConfig.deviceName));
    g_deviceConfig.diagEn = promptBool("Diagnostics BLE (next wakeup/photo dans scan response)", g_deviceConfig.diagEn);
}

static void menuBootWindow() {
    Serial.println(F("\n── Fenêtre de démarrage (CLI + interface web) ─────────────"));
    Serial.println(F("  Durée pendant laquelle le CLI série et le serveur web de"));
    Serial.println(F("  configuration sont actifs à chaque boot/réveil."));
    Serial.println(F("  5 secondes sont toujours garanties pour le CLI."));
    Serial.println(F("  0 = interface web désactivée (5 s CLI uniquement)."));
    g_deviceConfig.bootWindowSec = promptU8("Durée (0-180 s)",
                                            g_deviceConfig.bootWindowSec, 0, 180);
}

static void menuNtp() {
    Serial.println(F("\n── NTP & Fuseau horaire ───────────────────────────────────"));
    Serial.println(F("  Serveurs NTP : adresse IP ou nom d'hôte."));
    Serial.println(F("  Fuseau      : chaîne POSIX TZ, ex. CET-1CEST,M3.5.0,M10.5.0/3"));
    promptStr("Serveur NTP 1",       g_deviceConfig.ntpServer1,  sizeof(g_deviceConfig.ntpServer1));
    promptStr("Serveur NTP 2",       g_deviceConfig.ntpServer2,  sizeof(g_deviceConfig.ntpServer2));
    promptStr("Fuseau horaire (TZ)", g_deviceConfig.ntpTimezone, sizeof(g_deviceConfig.ntpTimezone));
}

// ─────────────────────────────────────────────────────────────────────────────
//  Diagnostic helpers
// ─────────────────────────────────────────────────────────────────────────────


// Returns true if WiFi is (or becomes) available.
// wasAlreadyConnected is set to true when WiFi was already up;
// in that case releaseWifi() will NOT disconnect (connection is owned externally).
static bool ensureWifi(bool& wasAlreadyConnected) {
    if (WiFi.status() == WL_CONNECTED) {
        wasAlreadyConnected = true;
        return true;
    }
    wasAlreadyConnected = false;
    return uploader::wifiConnect();
}

// Disconnect WiFi only if ensureWifi() connected it (wasAlreadyConnected == false).
static void releaseWifi(bool wasAlreadyConnected) {
    if (!wasAlreadyConnected) uploader::wifiDisconnect();
}

static void diagSyncNtp() {
    Serial.println(F("\n[WiFi] Vérification de la connexion..."));
    bool wasUp;
    if (!ensureWifi(wasUp)) {
        Serial.println(F("  ✗ Connexion Wi-Fi échouée !"));
        return;
    }
    Serial.printf("  ✓ IP : %s\r\n", WiFi.localIP().toString().c_str());
    Serial.println(F("[NTP]  Synchronisation en cours..."));
    nvs::begin();
    bool ok = time_manager::syncNtp();
    releaseWifi(wasUp);
    if (ok) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);
        char buf[32];
        strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &now_tm);
        Serial.printf("  ✓ Heure synchronisée : %s\r\n", buf);
    } else {
        Serial.println(F("  ✗ Synchronisation NTP échouée !"));
    }
}

static void diagWifi() {
    Serial.println(F("\n── Test connexion Wi-Fi ───────────────────────────────────"));
    Serial.printf("  SSID : %s\r\n", g_deviceConfig.wifiSsid);
    bool wasUp;
    if (!ensureWifi(wasUp)) {
        Serial.println(F("  ✗ Connexion échouée !"));
        return;
    }
    Serial.printf("  ✓ IP : %s  RSSI : %d dBm\r\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    if (wasUp) {
        Serial.println(F("  (Wi-Fi déjà actif – pas de déconnexion)"));
    } else {
        uploader::wifiDisconnect();
        Serial.println(F("  Déconnecté."));
    }
}

// Shared sensor reading + Serial printing helper.
// Reads all enabled sensors, prints results to Serial, builds BTHome payload.
// Sets anyRead to true if at least one valid value was obtained.
static BtHomePayload readAndPrintSensors(bool& anyRead) {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ_HZ);
    BtHomePayload payload = {};
    anyRead = false;

    if (g_deviceConfig.ds18b20Enabled) {
        nvs::begin();
        ds18b20::begin();
        auto readings = ds18b20::readAll();
        if (readings.empty()) {
            Serial.println(F("  DS18B20 : aucun capteur détecté"));
        }
        for (auto& r : readings) {
            if (r.valid) {
                Serial.printf("  DS18B20 [%02X%02X] : %.2f °C\r\n",
                              r.address[6], r.address[7], r.temperatureC);
                payload.temperatures.push_back(r.temperatureC);
                anyRead = true;
            }
        }
    } else {
        Serial.println(F("  DS18B20 : désactivé"));
    }

    if (g_deviceConfig.sht3xEnabled) {
        if (sht3x::begin()) {
            Sht3xReading r = sht3x::read();
            if (r.valid) {
                Serial.printf("  SHT3x   : %.2f °C  %.1f %%HR\r\n", r.temperatureC, r.humidityPct);
                payload.temperatures.push_back(r.temperatureC);
                payload.humidity    = r.humidityPct;
                payload.hasHumidity = true;
                anyRead = true;
            } else {
                Serial.println(F("  SHT3x   : lecture échouée"));
            }
        } else {
            Serial.println(F("  SHT3x   : non détecté sur I2C"));
        }
    } else {
        Serial.println(F("  SHT3x   : désactivé"));
    }

    if (g_deviceConfig.ina219Enabled) {
        if (ina219::begin()) {
            Ina219Reading r = ina219::read();
            if (r.valid) {
                Serial.printf("  INA219  : %.3f V  %.1f mA  %.1f mW\r\n",
                              r.busVoltageV, r.currentMa, r.powerMw);
                payload.voltage    = r.busVoltageV;
                payload.hasVoltage = true;
                payload.currentA   = r.currentMa / 1000.0f;
                payload.hasCurrent = true;
                payload.powerW     = r.powerMw / 1000.0f;
                payload.hasPower   = true;
                anyRead = true;
            } else {
                Serial.println(F("  INA219  : lecture échouée"));
            }
        } else {
            Serial.println(F("  INA219  : non détecté sur I2C"));
        }
    } else {
        Serial.println(F("  INA219  : désactivé"));
    }
    return payload;
}

static void diagSensorsOnly() {
    Serial.println(F("\n── Lecture des capteurs (sans BTHome) ────────────────────"));
    bool anyRead;
    readAndPrintSensors(anyRead);
    if (!anyRead) Serial.println(F("  ! Aucune donnée valide lue."));
}

static void diagSensorsWithBle() {
    Serial.println(F("\n── Capteurs + diffusion BTHome ────────────────────────────"));
    bool anyRead;
    BtHomePayload payload = readAndPrintSensors(anyRead);
    if (!anyRead) {
        Serial.println(F("  ! Aucune donnée valide – BTHome ignoré."));
        return;
    }
    bool wifiWasUp = (WiFi.status() == WL_CONNECTED);
    if (wifiWasUp) {
        Serial.println(F("[BLE]  Coupure Wi-Fi temporaire (~3 s)..."));
        uploader::wifiDisconnect();
    }
    bthome::begin();
    bthome::advertise(payload);
    bthome::end();
    Serial.println(F("  ✓ BTHome diffusé."));
    if (wifiWasUp) {
        Serial.println(F("[WiFi] (reconnexion à la prochaine opération réseau)"));
    }
}

static void diagDs18b20Discover() {
    Serial.println(F("\n── Découverte DS18B20 (scan + mémorisation NVS) ───────────"));
    nvs::begin();
    uint8_t found = ds18b20::discoverAndStore();
    if (found == 0) {
        Serial.println(F("  ✗ Aucun capteur DS18B20 détecté sur le bus."));
        return;
    }
    const auto& addrs = ds18b20::storedAddresses();
    Serial.printf("  ✓ %u capteur(s) détecté(s) et mémorisés :\r\n", found);
    for (size_t i = 0; i < addrs.size(); i++) {
        const auto& a = addrs[i];
        Serial.printf("    [%zu] %02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X\r\n",
                      i, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]);
    }
    Serial.println(F("  (IDs stockés en NVS – ordre utilisé aux boots suivants)"));
}

static void diagPhoto() {
    Serial.println(F("\n── Test photo + upload ─────────────────────────────────────"));
    bool wasUp;
    if (!ensureWifi(wasUp)) {
        Serial.println(F("  ✗ Wi-Fi échoué !"));
        return;
    }
    Serial.printf("  ✓ IP : %s\r\n", WiFi.localIP().toString().c_str());
    Serial.println(F("[CAM]  Init caméra..."));
    if (!camera_module::begin()) {
        Serial.println(F("  ✗ Init caméra échouée !"));
        releaseWifi(wasUp);
        return;
    }
    CameraFrame frame = camera_module::capture();
    if (!frame.valid) {
        Serial.println(F("  ✗ Capture échouée !"));
        camera_module::end();
        releaseWifi(wasUp);
        return;
    }
    Serial.printf("[UP]   Capture OK (%zu octets) → %s\r\n",
                  frame.len, g_deviceConfig.uploadEndpoint);
    UploadMetadata meta = {};
    meta.deviceId   = g_deviceConfig.deviceName;
    meta.timestampS = time_manager::nowEpoch();
    meta.latitude   = 0.0f;
    meta.longitude  = 0.0f;
    int code = uploader::uploadPhoto(frame.buf, frame.len, meta);
    camera_module::releaseFrame(frame);
    camera_module::end();
    releaseWifi(wasUp);
    if (code >= 200 && code < 300) {
        Serial.printf("  ✓ Upload réussi (HTTP %d)\r\n", code);
    } else {
        Serial.printf("  ✗ Upload échoué (HTTP %d)\r\n", code);
    }
}

static void diagMqttAlive() {
    Serial.println(F("\n── MQTT ping \"I'm alive\" ──────────────────────────────────"));
    if (!g_deviceConfig.mqttEnabled || g_deviceConfig.mqttBroker[0] == '\0') {
        Serial.println(F("  MQTT non configuré – voir menu [5] MQTT."));
        return;
    }
    Serial.printf("  Broker : %s:%u\r\n", g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort);
    bool wasUp;
    if (!ensureWifi(wasUp)) {
        Serial.println(F("  ✗ Wi-Fi échoué !"));
        return;
    }
    bool ok = mqtt_config::publishAlive();
    releaseWifi(wasUp);
    Serial.println(ok ? F("  ✓ Ping envoyé.") : F("  ✗ Échec du ping MQTT."));
}

static void diagWebcam() {
    Serial.println(F("\n── Mode Webcam (MJPEG HTTP) ────────────────────────────────"));
    bool wasUp;
    if (!ensureWifi(wasUp)) {
        Serial.println(F("  ✗ Wi-Fi échoué !"));
        return;
    }
    Serial.printf("  ✓ IP : %s\r\n", WiFi.localIP().toString().c_str());

    if (!camera_module::begin()) {
        Serial.println(F("  ✗ Init caméra échouée !"));
        releaseWifi(wasUp);
        return;
    }

    WiFiServer server(80);
    server.begin();

    String ip = WiFi.localIP().toString();
    Serial.printf("  ✓ Serveur démarré.\r\n");
    Serial.printf("    MJPEG stream  : http://%s/stream\r\n", ip.c_str());
    Serial.printf("    Capture JPEG  : http://%s/capture\r\n", ip.c_str());
    Serial.println(F("  Appuyez sur Entrée pour arrêter."));

    // Drain any buffered keystroke that opened this menu
    while (Serial.available()) Serial.read();

    while (true) {
        // Exit on any key
        if (Serial.available()) {
            while (Serial.available()) Serial.read();
            break;
        }

        WiFiClient client = server.accept();
        if (!client) { delay(10); continue; }

        // Read first request line
        String reqLine;
        uint32_t t0 = millis();
        while (client.connected() && !client.available()) {
            if (millis() - t0 > 2000) break;
            delay(1);
        }
        while (client.available()) {
            char ch = (char)client.read();
            if (ch == '\n') break;
            reqLine += ch;
        }
        // Consume remaining headers
        while (client.available()) client.read();

        bool isStream  = reqLine.indexOf("/stream")  >= 0;
        bool isCapture = reqLine.indexOf("/capture") >= 0;
        bool isRoot    = (!isStream && !isCapture);

        if (isRoot) {
            // Simple HTML page with auto-refreshing image
            client.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"));
            client.printf(
                "<!DOCTYPE html><html><head><title>ESP32-S3 Webcam</title>"
                "<meta http-equiv='refresh' content='2'></head><body>"
                "<h2>ESP32-S3 Webcam</h2>"
                "<img src='/capture' style='max-width:100%%'><br>"
                "<small>Rafraîchissement auto toutes les 2s – "
                "<a href='/stream'>Stream MJPEG</a></small>"
                "</body></html>");
        } else if (isCapture) {
            CameraFrame frame = camera_module::capture();
            if (frame.valid) {
                client.printf(
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: image/jpeg\r\n"
                    "Content-Length: %zu\r\n"
                    "Connection: close\r\n\r\n", frame.len);
                client.write(frame.buf, frame.len);
                camera_module::releaseFrame(frame);
            } else {
                client.print(F("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n"));
            }
        } else if (isStream) {
            // MJPEG multipart stream
            client.print(F(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                "Connection: keep-alive\r\n\r\n"));

            while (client.connected()) {
                if (Serial.available()) { while (Serial.available()) Serial.read(); break; }

                CameraFrame frame = camera_module::capture();
                if (!frame.valid) { delay(100); continue; }

                client.printf(
                    "--frame\r\n"
                    "Content-Type: image/jpeg\r\n"
                    "Content-Length: %zu\r\n\r\n", frame.len);
                client.write(frame.buf, frame.len);
                client.print(F("\r\n"));
                camera_module::releaseFrame(frame);
                delay(100); // ~10 fps max
            }
        } else {
            client.print(F("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"));
        }

        client.stop();
    }

    server.end();
    camera_module::end();
    releaseWifi(wasUp);
    Serial.println(F("  Mode webcam terminé."));
}

static void menuDiag() {
    while (true) {
        Serial.println(F("\n╔══════════════════════════════════════════════════════╗"));
        Serial.println(F("║                  Diagnostics                        ║"));
        Serial.println(F("╠══════════════════════════════════════════════════════╣"));
        Serial.println(F("║  [W]  Test connexion Wi-Fi                          ║"));
        Serial.println(F("║  [N]  Synchronisation NTP                           ║"));
        Serial.println(F("║  [C]  Forcer photo + upload                         ║"));
        Serial.println(F("║  [M]  MQTT ping \"I'm alive\"                         ║"));
        Serial.println(F("║  [S]  Lecture capteurs (sans BTHome)                ║"));
        Serial.println(F("║  [B]  Capteurs + diffusion BTHome (coupe Wi-Fi)     ║"));
        Serial.println(F("║  [I]  Découverte DS18B20 (scan + mémorise NVS)      ║"));
        Serial.println(F("║  [V]  Webcam live (MJPEG HTTP, focus/cadrage)        ║"));
        Serial.println(F("║  [X]  Retour au menu principal                      ║"));
        Serial.println(F("╚══════════════════════════════════════════════════════╝"));
        Serial.print(F("Choix: "));

        String choice = readLine(120000);
        choice.trim();
        if (choice.isEmpty()) continue;
        char c = (char)toupper((unsigned char)choice[0]);

        switch (c) {
            case 'W': diagWifi();            break;
            case 'N': diagSyncNtp();         break;
            case 'C': diagPhoto();           break;
            case 'M': diagMqttAlive();       break;
            case 'S': diagSensorsOnly();     break;
            case 'B': diagSensorsWithBle();  break;
            case 'I': diagDs18b20Discover(); break;
            case 'V': diagWebcam();          break;
            case 'X': return;
            default:  Serial.printf("  Option '%c' inconnue.\r\n", c); break;
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  Main interactive menu
// ─────────────────────────────────────────────────────────────────────────────

static void runMenu() {
    bool dirty = false;

    while (true) {
        Serial.println(F("\n╔══════════════════════════════════════════════════════════╗"));
        Serial.println(F("║           ESP32-S3  \u2013  Configuration                    ║"));
        Serial.println(F("╠══════════════════════════════════════════════════════════╣"));
        Serial.println(F("║  [1]  Capteurs (présence & intervalles)               ║"));
        Serial.println(F("║  [2]  Planification photo                             ║"));
        Serial.println(F("║  [3]  Réseau (Wi-Fi / endpoint)                       ║"));
        Serial.println(F("║  [4]  OTA                                             ║"));
        Serial.println(F("║  [5]  MQTT (config à distance)                        ║"));
        Serial.println(F("║  [6]  Nom BLE                                         ║"));
        Serial.println(F("║  [7]  Fenêtre de démarrage (CLI + web)                ║"));
        Serial.println(F("║  [8]  NTP & Fuseau horaire                            ║"));
        Serial.println(F("║  [P]  Afficher la configuration actuelle              ║"));
        Serial.println(F("║  [D]  Diagnostics (WiFi / NTP / photo / capteurs)     ║"));
        Serial.println(F("║  [S]  Sauvegarder et reprendre le boot                ║"));
        Serial.println(F("║  [R]  Réinitialiser aux valeurs par défaut            ║"));
        Serial.println(F("║  [X]  Reprendre sans sauvegarder                     ║"));
        Serial.println(F("╚══════════════════════════════════════════════════════════╝"));
        Serial.print(F("Choix: "));

        String choice = readLine(120000);   // 2 min timeout waiting for a choice
        choice.trim();
        if (choice.isEmpty()) continue;

        char c = (char)toupper((unsigned char)choice[0]);

        switch (c) {
            case '1': menuSensors(); dirty = true; break;
            case '2': menuPhoto();   dirty = true; break;
            case '3': menuNetwork(); dirty = true; break;
            case '4': menuOta();     dirty = true; break;
            case '5': menuMqtt();    dirty = true; break;
            case '6': menuBle();         dirty = true; break;
            case '7': menuBootWindow();  dirty = true; break;
            case '8': menuNtp();         dirty = true; break;

            case 'P':
                device_config::print();
                break;

            case 'D':
                menuDiag();
                break;

            case 'S':
                device_config::save();
                Serial.println(F("✓ Configuration sauvegardée."));
                Serial.println(F("  Synchronisation NTP pour calibrer le scheduler..."));
                diagSyncNtp();
                return;

            case 'R':
                device_config::resetToDefaults();
                Serial.println(F("↺ Valeurs par défaut restaurées (non sauvegardées – appuyez S pour sauvegarder)."));
                dirty = true;
                break;

            case 'X':
                if (dirty) Serial.println(F("! Modifications non sauvegardées ignorées."));
                Serial.println(F("→ Reprise du boot..."));
                return;

            default:
                Serial.printf("  Option '%c' inconnue.\r\n", c);
                break;
        }
    }
}

void serial_cli::offerConfigWindow() {
    const uint32_t kSafetyMs = 5000;   // minimum CLI window regardless of setting
    uint32_t bootWindowMs = (uint32_t)g_deviceConfig.bootWindowSec * 1000;
    uint32_t totalMs      = (bootWindowMs < kSafetyMs) ? kSafetyMs : bootWindowMs;
    bool     webEnabled   = (g_deviceConfig.bootWindowSec > 0);

    // ── Connect WiFi once for the entire config window ───────────────────
    // WiFi stays alive throughout CLI + web UI so diagnostics need no reconnect.
    bool wifiConnected = false;
    if (webEnabled) {
        Serial.println(F("[Config] Connexion Wi-Fi (fenêtre de configuration)..."));
        wifiConnected = uploader::wifiConnect();
        if (wifiConnected) {
            Serial.printf("[Config] Wi-Fi : %s\r\n", WiFi.localIP().toString().c_str());
        } else {
            Serial.println(F("[Config] Wi-Fi indisponible – mode CLI série uniquement."));
        }
    }

    // ── Auto-sync MQTT config while Wi-Fi is already up ──────────────────────
    if (wifiConnected && g_deviceConfig.mqttEnabled) {
        Serial.println(F("[Config] Synchronisation de la config MQTT..."));
        bool updated = mqtt_config::syncFromBroker();
        Serial.println(updated
            ? F("[Config] Config MQTT appliquée et sauvegardée.")
            : F("[Config] Pas de mise à jour MQTT disponible."));
    }

    // ── Auto-check OTA ────────────────────────────────────────────────────────
    // If newer firmware is found it is applied and the device reboots; otherwise
    // execution falls through to the interactive config window.
    if (wifiConnected && g_deviceConfig.otaEnabled) {
        Serial.println(F("[Config] Vérification OTA..."));
        ota::checkAndApply();   // reboots on success; returns false when up-to-date
        Serial.println(F("[Config] Firmware à jour."));
    }

    Serial.printf("[Config] Fenêtre : %u s – appuyez sur une touche pour le menu CLI.\r\n",
                  totalMs / 1000);
    Serial.flush();
    while (Serial.available()) Serial.read();

    // ── Start HTTP config server on port 80 ──────────────────────────────
    WiFiServer server(80);
    bool serverStarted = false;
    if (wifiConnected) {
        server.begin();
        serverStarted = true;
        Serial.printf("[Config] Interface web : http://%s/\r\n",
                      WiFi.localIP().toString().c_str());
    }

    uint32_t deadline    = millis() + totalMs;
    bool     cliMode     = false;
    bool     webHandled  = false;
    bool     serverPaused = false;

    // Outer loop re-enters after the deadline if a /pause request arrives in the
    // final milliseconds (race-condition drain: one extra handleBootWebClient()
    // is called after each inner-loop exit to catch any pending connection).
    do {
    while (serverPaused || millis() < deadline) {
        // CLI keypress → stop HTTP server (free port 80) but keep WiFi alive
        // so the CLI diagnostics can use it without reconnecting.
        if (Serial.available()) {
            while (Serial.available()) Serial.read();
            if (serverStarted) { server.end(); serverStarted = false; }
            cliMode = true;
            break;
        }

        if (serverStarted) {
            // When paused, pass -1 so a page reload shows "en pause" instead of
            // a stale or very-large countdown value.
            int secsLeft = serverPaused ? -1 : (int)((deadline - millis()) / 1000);
            int action   = handleBootWebClient(server, secsLeft, deadline, serverPaused);
            if (action == 1) {
                server.end(); serverStarted = false;
                Serial.println(F("[Config] Configuration sauvegardée – boot en cours."));
                webHandled = true;
                break;
            } else if (action == 2) {
                server.end(); serverStarted = false;
                Serial.println(F("[Config] Configuration sauvegardée – redémarrage..."));
                Serial.flush();
                if (wifiConnected) uploader::wifiDisconnect();
                delay(300);
                ESP.restart();
                // never reached
            }
        }

        delay(10);
    }
    // ── Drain: process one extra pending client that may have arrived at the ─
    // ── exact deadline boundary (e.g. a /pause click in the last second).   ─
    if (serverStarted && !cliMode && !webHandled) {
        handleBootWebClient(server, -1, deadline, serverPaused);
    }
    } while (serverPaused && !cliMode && !webHandled);

    if (serverStarted) { server.end(); serverStarted = false; }

    // ── CLI menu (WiFi stays up so all diag functions work without reconnect) ─
    if (cliMode) {
        device_config::print();
        runMenu();
    } else if (!webHandled) {
        Serial.println(F("[Config] Délai expiré – boot normal."));
    }

    // ── Disconnect WiFi (owned by this function only) ────────────────────
    if (wifiConnected) uploader::wifiDisconnect();
}

