import json
import os
import sys

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

sys.path.append(os.path.join(ROOT_DIR, "drone_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "drone_system.settings")

import django  # noqa: E402

django.setup()

from app_core.models import TelemetryLog  # noqa: E402

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
# listener ใช้ admin — EMQX บล็อก subscribe "#" แต่อนุญาต topic ของ DJI ได้
MQTT_USER = os.getenv("MQTT_LISTENER_USER", os.getenv("MQTT_USER", "admin"))
MQTT_PASS = os.getenv("MQTT_LISTENER_PASSWORD", os.getenv("MQTT_PASSWORD", "admin1234"))

# DJI Cloud API — Pilot to Cloud OSD topics
# https://github.com/dji-sdk/Cloud-API-Doc/blob/master/docs/en/60.api-reference/10.pilot-to-cloud/00.mqtt/00.topic-definition.md
MQTT_TOPICS = [
    "thing/product/+/osd",
    "thing/product/+/state",
    "sys/product/+/status",
]


def _extract_coords(payload: dict) -> tuple[str | None, float | None, float | None, float | None]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None, None, None, None

    lat = data.get("latitude")
    lng = data.get("longitude")
    alt = data.get("height") or data.get("elevation") or data.get("altitude")

    drone_sn = payload.get("gateway")
    if not drone_sn:
        for key in ("sn", "device_sn"):
            if data.get(key):
                drone_sn = data[key]
                break

    return drone_sn, lat, lng, alt


def _sn_from_topic(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] == "thing" and parts[1] == "product":
        return parts[2]
    if len(parts) >= 3 and parts[0] == "sys" and parts[1] == "product":
        return parts[2]
    return "M4E"


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or str(reason_code) == "Success":
        print("✅ เชื่อมต่อ MQTT Broker (EMQX) สำเร็จ!")
        for topic in MQTT_TOPICS:
            client.subscribe(topic, qos=0)
            print(f"📡 Subscribe: {topic}")
    else:
        print(f"❌ เชื่อมต่อล้มเหลว: {reason_code}")


def on_subscribe(client, userdata, mid, granted_qos, properties=None):
    for qos in granted_qos:
        code = getattr(qos, "value", qos)
        if code != 0 and str(qos) != "Granted QoS 0":
            print(f"⚠️ Subscribe ถูกปฏิเสธ: {qos}")
        else:
            print(f"✅ Subscribe OK (mid={mid})")


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload_str = msg.payload.decode("utf-8")
        payload = json.loads(payload_str)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"⚠️ แปลง payload ไม่ได้ [{topic}]: {exc}")
        return

    print(f"📩 {topic} ({len(payload_str)} bytes)")

    if not topic.endswith("/osd") and "/osd" not in topic:
        return

    drone_sn, lat, lng, alt = _extract_coords(payload)
    if lat is None or lng is None:
        keys = list((payload.get("data") or {}).keys())[:8]
        print(f"   ↳ ไม่มี lat/lng ใน OSD (keys: {keys}...)")
        return

    sn = drone_sn or _sn_from_topic(topic)
    TelemetryLog.objects.create(
        drone_sn=sn,
        latitude=float(lat),
        longitude=float(lng),
        altitude=float(alt) if alt is not None else 0.0,
    )
    print(f"📥 บันทึกพิกัด SN={sn} lat={lat} lng={lng} alt={alt}")


def main():
    print(f"🔌 MQTT {MQTT_HOST}:{MQTT_PORT} user={MQTT_USER}")
    print("ℹ️  EMQX บล็อก subscribe '#' — ใช้ topic DJI โดยตรง")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="telemetry_listener")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 ปิด Telemetry Listener")


if __name__ == "__main__":
    main()
