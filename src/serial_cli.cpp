#include "serial_cli.h"
#include "device_config.h"
#include <Arduino.h>
#include <string.h>

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
            if (c == '\r') continue;
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
            if (c == '\r') continue;
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
        Serial.printf("  ! Out of range – keeping %u\n", current);
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
            else Serial.printf("  ! Hors plage – port conservé (%u)\n", g_deviceConfig.mqttPort);
        }

        promptStr("Utilisateur MQTT (vide=aucun)", g_deviceConfig.mqttUser, sizeof(g_deviceConfig.mqttUser));
        promptStr("Mot de passe MQTT",             g_deviceConfig.mqttPassword, sizeof(g_deviceConfig.mqttPassword), true);
        promptStr("Client ID MQTT",                g_deviceConfig.mqttClientId, sizeof(g_deviceConfig.mqttClientId));
    }
}

static void menuBle() {
    Serial.println(F("\n── BLE ────────────────────────────────────────────────────"));
    promptStr("Nom BLE de l'appareil", g_deviceConfig.deviceName, sizeof(g_deviceConfig.deviceName));
}

// ─────────────────────────────────────────────────────────────────────────────
//  Main interactive menu
// ─────────────────────────────────────────────────────────────────────────────

static void runMenu() {
    bool dirty = false;

    while (true) {
        Serial.println(F("\n╔══════════════════════════════════════════════════════╗"));
        Serial.println(F("║          ESP32-S3  –  Configuration                 ║"));
        Serial.println(F("╠══════════════════════════════════════════════════════╣"));
        Serial.println(F("║  [1]  Capteurs (présence & intervalles)             ║"));
        Serial.println(F("║  [2]  Planification photo                           ║"));
        Serial.println(F("║  [3]  Réseau (Wi-Fi / endpoint)                     ║"));
        Serial.println(F("║  [4]  OTA                                           ║"));
        Serial.println(F("║  [5]  MQTT (config à distance)                      ║"));
        Serial.println(F("║  [6]  Nom BLE                                       ║"));
        Serial.println(F("║  [P]  Afficher la configuration actuelle            ║"));
        Serial.println(F("║  [S]  Sauvegarder et reprendre le boot              ║"));
        Serial.println(F("║  [R]  Réinitialiser aux valeurs par défaut          ║"));
        Serial.println(F("║  [X]  Reprendre sans sauvegarder                   ║"));
        Serial.println(F("╚══════════════════════════════════════════════════════╝"));
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
            case '6': menuBle();     dirty = true; break;

            case 'P':
                device_config::print();
                break;

            case 'S':
                device_config::save();
                Serial.println(F("✓ Configuration sauvegardée."));
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
                Serial.printf("  Option '%c' inconnue.\n", c);
                break;
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  Public API
// ─────────────────────────────────────────────────────────────────────────────

void serial_cli::offerConfigWindow(uint32_t windowMs) {
    Serial.printf("[Config] Appuyez sur une touche dans %u ms pour ouvrir le menu de configuration...\n",
                  windowMs);
    Serial.flush();

    uint32_t deadline = millis() + windowMs;
    while (millis() < deadline) {
        if (Serial.available()) {
            while (Serial.available()) Serial.read();   // flush
            device_config::print();
            runMenu();
            return;
        }
        delay(50);
    }
    Serial.println(F("[Config] Aucune touche – configuration actuelle conservée."));
}
