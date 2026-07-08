import json
import os
import sys
import time

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
MQTT_USER = os.getenv("MQTT_LISTENER_USER", os.getenv("MQTT_USER", "admin"))
MQTT_PASS = os.getenv("MQTT_LISTENER_PASSWORD", os.getenv("MQTT_PASSWORD", "admin1234"))

# DJI Cloud API topics
MQTT_TOPICS = [
    "thing/product/+/osd",
    "thing/product/+/state",
    "sys/product/+/status",
]

# gateway_sn -> aircraft_sn (จาก update_topo)
known_aircraft: dict[str, str] = {}


def _sn_from_topic(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) >= 3 and parts[1] == "product":
        return parts[2]
    return "M4E"


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


def _save_telemetry(sn: str, lat: float, lng: float, alt: float | None, source: str) -> None:
    TelemetryLog.objects.create(
        drone_sn=sn,
        latitude=float(lat),
        longitude=float(lng),
        altitude=float(alt) if alt is not None else 0.0,
    )
    print(f"📥 [{source}] SN={sn} lat={lat} lng={lng} alt={alt}")


def _reply_update_topo(client: mqtt.Client, gateway_sn: str, payload: dict) -> None:
    """DJI ต้องได้รับ status_reply ก่อนถึงจะเริ่มส่ง OSD พิกัด"""
    reply_topic = f"sys/product/{gateway_sn}/status_reply"
    reply = {
        "tid": payload.get("tid"),
        "bid": payload.get("bid"),
        "timestamp": int(time.time() * 1000),
        "method": payload.get("method", "update_topo"),
        "data": {"result": 0},
    }
    client.publish(reply_topic, json.dumps(reply), qos=1)
    print(f"📤 ตอบ status_reply → {reply_topic}")

    data = payload.get("data") or {}
    sub_devices = data.get("sub_devices") or []
    if sub_devices:
        aircraft_sn = sub_devices[0].get("sn")
        if aircraft_sn:
            known_aircraft[gateway_sn] = aircraft_sn
            print(f"🔗 Topology: RC={gateway_sn} → โดรน={aircraft_sn}")
    else:
        known_aircraft.pop(gateway_sn, None)
        print(f"🔗 Topology: RC={gateway_sn} (ไม่มีโดรน online)")


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
        if str(qos) != "Granted QoS 0" and getattr(qos, "value", qos) != 0:
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

    # --- sys/product/{gateway}/status : ต้องตอบ status_reply ---
    if topic.startswith("sys/product/") and topic.endswith("/status"):
        method = payload.get("method", "")
        gateway_sn = _sn_from_topic(topic)
        print(f"📩 {topic} method={method}")
        if method == "update_topo":
            _reply_update_topo(client, gateway_sn, payload)
        return

    # --- thing/product/{sn}/osd หรือ /state ---
    if "/osd" not in topic and not topic.endswith("/state"):
        return

    print(f"📩 {topic} ({len(payload_str)} bytes)")

    drone_sn, lat, lng, alt = _extract_coords(payload)
    if lat is None or lng is None:
        keys = list((payload.get("data") or {}).keys())[:10]
        print(f"   ↳ ไม่มี lat/lng (keys: {keys})")
        return

    sn = drone_sn or _sn_from_topic(topic)
    _save_telemetry(sn, lat, lng, alt, "osd" if "/osd" in topic else "state")


def main():
    print(f"🔌 MQTT {MQTT_HOST}:{MQTT_PORT} user={MQTT_USER}")
    print("ℹ️  รอ update_topo จาก RC → ตอบ status_reply → รับ OSD พิกัด")

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
