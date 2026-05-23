from __future__ import annotations

import json
import ssl
from typing import Any

import paho.mqtt.client as mqtt

from .config import ServiceConfig


class MqttPublisher:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config

    def publish_discovery(self) -> None:
        if not self.config.mqtt_enabled:
            return
        object_id = self._object_id()
        sensors = {
            f"sensor/{object_id}_level/config": {
                "name": f"{self.config.device_name} Level",
                "unique_id": f"{object_id}_level",
                "state_topic": self.config.state_topic,
                "unit_of_measurement": "%",
                "device_class": None,
                "value_template": "{{ value_json.percentage }}",
            },
            f"sensor/{object_id}_confidence/config": {
                "name": f"{self.config.device_name} Confidence",
                "unique_id": f"{object_id}_confidence",
                "state_topic": self.config.state_topic,
                "unit_of_measurement": "%",
                "icon": "mdi:chart-bell-curve-cumulative",
                "value_template": "{{ value_json.confidence }}",
            },
            f"binary_sensor/{object_id}_estimated/config": {
                "name": f"{self.config.device_name} Estimated",
                "unique_id": f"{object_id}_estimated",
                "state_topic": self.config.state_topic,
                "payload_on": "true",
                "payload_off": "false",
                "value_template": "{{ value_json.estimated | string | lower }}",
                "icon": "mdi:calculator-variant",
            },
        }

        client = self._connect()
        device = {
            "name": self.config.device_name,
            "identifiers": [self._object_id()],
            "manufacturer": "remiserriere",
            "model": "Gauge vision service",
            "sw_version": "1",
        }
        for topic_suffix, payload in sensors.items():
            payload["device"] = device
            payload["json_attributes_topic"] = self.config.state_topic
            client.publish(f"{self.config.home_assistant_discovery_prefix}/{topic_suffix}", json.dumps(payload), retain=True)
        client.disconnect()

    def publish_state(self, record: dict[str, Any]) -> None:
        if not self.config.mqtt_enabled:
            return
        analysis = record["analysis"]
        payload = {
            "record_id": record["id"],
            "received_at": record["received_at"],
            "percentage": analysis["percentage"],
            "confidence": analysis["confidence"],
            "estimated": analysis["estimated"],
            "needle_angle": analysis["needle_angle"],
            "low_angle": analysis["low_angle"],
            "high_angle": analysis["high_angle"],
            "circle": analysis["circle"],
            "source": analysis["source"],
            "image_url": record["image_url"],
            "metadata": record["metadata"],
        }
        client = self._connect()
        client.publish(self.config.state_topic, json.dumps(payload), retain=True)
        client.disconnect()

    def _connect(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.config.mqtt_client_id)
        if self.config.mqtt_username:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        if self.config.mqtt_use_ssl:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.connect(self.config.mqtt_host, self.config.mqtt_port, 30)
        return client

    def _object_id(self) -> str:
        return self.config.mqtt_client_id.replace("/", "_").replace(" ", "_")
