"""DJI DRC (Drone Remote Control) service — ควบคุมโดรนระยะไกลผ่าน MQTT + WebSocket

Flow: cloud_control_auth_request → drc_mode_enter → (ยืนยันจาก drc/up traffic
จริง เพราะ drc_status_notify ไม่มาจากโดรนรุ่นนี้) → drone_control loop (5-10Hz)

Safety watchdogs: joystick staleness (zero-velocity อัตโนมัติ), ไม่มี browser
เชื่อมต่อเกิน grace period (ปล่อยสิทธิ์อัตโนมัติ), shutdown/SIGTERM cleanup,
rate cap ≤10Hz, emergency stop บายพาสคิวปกติ
"""

import asyncio
import atexit
import json
import os
import signal
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import websockets
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

sys.path.append(os.path.join(ROOT_DIR, "drone_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "drone_system.settings")

import django  # noqa: E402

django.setup()

from app_core.models import FlightControlEvent, TelemetryLog  # noqa: E402

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
DRC_MQTT_USER = os.getenv("DRC_MQTT_USER", "drc_service")
DRC_MQTT_PASSWORD = os.getenv("DRC_MQTT_PASSWORD", "")
SERVER_IP = os.getenv("SERVER_IP", "127.0.0.1")

WS_PORT = int(os.getenv("DRC_WS_PORT", 8010))
HEARTBEAT_INTERVAL = float(os.getenv("DRC_HEARTBEAT_INTERVAL", 2))
DISCONNECT_GRACE_SECONDS = float(os.getenv("DRC_DISCONNECT_GRACE_SECONDS", 3))
STALE_THRESHOLD = float(os.getenv("DRC_STALE_THRESHOLD_SECONDS", 0.5))

USER_ID = "web-remote-control"
USER_CALLSIGN = "Web Operator"
# M3E/M4E series: สิทธิ์บิน ("flight") ครอบคลุมการควบคุม payload (กล้อง/gimbal) อยู่แล้ว
# (doc DJI: "no distinction between the flight control authority and payload control authority")
# ไม่ใส่ "payload" แยก เพราะ M4E อาจ reject key ที่ไม่รู้จักแล้วทำให้ auth ล้มทั้งคำขอ
CONTROL_KEYS = ["flight"]

# payload_index เริ่มต้น (M4E). ถ้า drc/up ส่ง drc_camera_osd_info_push มา จะ auto-update
# เป็นค่าจริงจากโดรน — override ผ่าน .env: DRC_PAYLOAD_INDEX
DEFAULT_PAYLOAD_INDEX = os.getenv("DRC_PAYLOAD_INDEX", "88-0-0")
GIMBAL_PITCH_LIMIT = float(os.getenv("DRC_GIMBAL_PITCH_LIMIT", 0.5))  # rad/s
# กล้อง M4E: gimbal เป็นแบบ pitch อย่างเดียว (ก้ม-เงย) — ไม่มีแกน yaw ให้ผู้ใช้คุม
# การหันซ้าย-ขวาต้องหันตัวโดรน (locked=true) เท่านั้น
GIMBAL_TICK_HZ = 10.0
GIMBAL_TICK_INTERVAL = 1.0 / GIMBAL_TICK_HZ
CAMERA_AIM_MIN_INTERVAL = 0.2  # ส่ง camera_aim ได้ถี่สุด 5Hz (ตอน follow)

# ---------- Phase B: joystick / drone_control ----------
# ตาม doc DJI: ส่ง 5-10Hz — ใช้ 7Hz เป็นค่ากลาง และ "ห้ามเกิน 10Hz" คือ hard cap ห้ามแก้เกินนี้
CONTROL_TICK_HZ = 7.0
CONTROL_TICK_INTERVAL = max(1.0 / 10.0, 1.0 / CONTROL_TICK_HZ)  # ไม่มีทางถี่เกิน 10Hz แม้แก้ CONTROL_TICK_HZ
XY_LIMIT = 17.0  # m/s ตาม doc (ซ้าย-ขวา, หน้า-หลัง)
# หมายเหตุ: doc DJI ระบุ h range แปลกๆ (min:5, max:-4) ซึ่งดูเหมือน error ในเอกสาร
# ใช้ค่า conservative แทนที่จะเชื่อ range ที่ผิดปกติจากเอกสาร
H_LIMIT = 4.0  # m/s ขึ้น-ลง (conservative, ไม่ได้อ้างอิง range ที่ผิดปกติในเอกสาร)
W_LIMIT = 90.0  # deg/s หมุนตัว ตาม doc


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


class DrcController:
    """เก็บ state ทั้งหมดของ DRC session เดียว (รองรับโดรนตัวเดียวต่อครั้ง)"""

    def __init__(self):
        self.gateway_sn: str | None = None
        # idle -> requesting -> granted -> drc_connecting -> drc_connected
        self.control_state = "idle"
        self.drc_state = 0  # 0=not connected,1=connecting,2=connected ตาม DJI enum
        self.last_message = "รอข้อมูลโดรน (gateway_sn) จาก telemetry_listener..."
        self.ws_clients: set = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mqtt_client: mqtt.Client | None = None
        self.last_ws_disconnect_at: float | None = None
        self._lock = threading.Lock()

        # Phase B: joystick state
        self.joystick = {"x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0}
        self.joystick_updated_at = 0.0
        self.control_seq = 0

        # Phase C: gimbal / camera state
        self.payload_index = DEFAULT_PAYLOAD_INDEX
        self.gimbal = {"pitch": 0.0}  # rad/s (pitch อย่างเดียว — M4E ไม่มี gimbal yaw)
        self.gimbal_updated_at = 0.0
        self._last_gimbal_zero_sent = True
        self.follow_active = False
        self.last_aim_at = 0.0

    # ---------- audit log ----------
    def log_event(self, event_type: str, detail: str = "") -> None:
        # Django ORM ห้ามเรียกแบบ sync ตรงๆ จาก asyncio event loop thread (SynchronousOnlyOperation)
        # เขียนใน thread แยกเสมอ ไม่ว่าจะถูกเรียกจาก paho callback หรือ ws handler ก็ตาม
        def _write():
            try:
                FlightControlEvent.objects.create(
                    drone_sn=self.gateway_sn or "unknown",
                    event_type=event_type,
                    detail=detail[:255] if detail else "",
                )
            except Exception as exc:  # noqa: BLE001 - audit log ต้องไม่ทำให้ service ล้ม
                print(f"⚠️ log_event ล้มเหลว: {exc}")

        threading.Thread(target=_write, daemon=True).start()

    # ---------- broadcast ไปหา browser ----------
    def set_status(self, control_state: str | None = None, drc_state: int | None = None,
                   message: str | None = None) -> None:
        if control_state is not None:
            self.control_state = control_state
        if drc_state is not None:
            self.drc_state = drc_state
        if message is not None:
            self.last_message = message
        self.broadcast({
            "type": "status",
            "control_state": self.control_state,
            "drc_state": self.drc_state,
            "gateway_sn": self.gateway_sn,
            "message": self.last_message,
        })

    def broadcast(self, payload: dict) -> None:
        if not self.loop:
            return
        data = json.dumps(payload)
        for ws in list(self.ws_clients):
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, data), self.loop)

    @staticmethod
    async def _safe_send(ws, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:  # noqa: BLE001 - client อาจหลุดไปแล้ว
            pass

    # ---------- MQTT publish helpers ----------
    def _publish_service(self, method: str, data: dict) -> None:
        if not self.mqtt_client or not self.gateway_sn:
            print(f"⚠️ ส่ง {method} ไม่ได้ — ยังไม่มี mqtt_client หรือ gateway_sn")
            return
        topic = f"thing/product/{self.gateway_sn}/services"
        payload = {
            "tid": _new_id(),
            "bid": _new_id(),
            "timestamp": _now_ms(),
            "method": method,
            "data": data,
        }
        self.mqtt_client.publish(topic, json.dumps(payload), qos=1)
        print(f"📤 [{method}] → {topic}")

    def request_control(self) -> None:
        if not self.gateway_sn:
            self.set_status(message="ยังไม่รู้ gateway_sn ของโดรน — ตรวจสอบว่า telemetry_listener รันอยู่")
            return
        self.log_event("requested")
        self.set_status(control_state="requesting", message="ขอสิทธิ์ควบคุม — รอกดยืนยันที่ RC...")
        self._publish_service("cloud_control_auth_request", {
            "user_id": USER_ID,
            "user_callsign": USER_CALLSIGN,
            "control_keys": CONTROL_KEYS,
        })

    def enter_drc_mode(self) -> None:
        self.set_status(control_state="drc_connecting", message="เข้าสู่ DRC mode...")
        self._publish_service("drc_mode_enter", {
            "mqtt_broker": {
                "address": f"{SERVER_IP}:{MQTT_PORT}",
                "client_id": f"drc-{self.gateway_sn}",
                "username": DRC_MQTT_USER,
                "password": DRC_MQTT_PASSWORD,
                "expire_time": int(time.time()) + 86400,
                "enable_tls": False,
            },
            "osd_frequency": 10,
            "hsi_frequency": 5,
        })

    def exit_drc_mode(self) -> None:
        self._publish_service("drc_mode_exit", {})

    def release_control(self, event_type: str = "released", detail: str = "") -> None:
        was_connected = self.control_state == "drc_connected"
        with self._lock:
            self.joystick = {"x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0}
            self.joystick_updated_at = time.time()
            self.gimbal = {"pitch": 0.0}
            self.gimbal_updated_at = time.time()
            self._last_gimbal_zero_sent = False
        self.follow_active = False
        if was_connected:
            # ยิง zero-velocity ทันที ไม่รอ tick ถัดไป (สำคัญตอน shutdown ที่ tick loop อาจไม่มีโอกาสรันอีก)
            self.publish_drone_control()
        if self.control_state in ("drc_connected", "drc_connecting"):
            self.exit_drc_mode()
        if self.gateway_sn:
            self._publish_service("cloud_control_release", {"control_keys": CONTROL_KEYS})
        self.log_event(event_type, detail)
        self.set_status(control_state="idle", drc_state=0, message="ปล่อยสิทธิ์ควบคุมแล้ว")

    # ---------- heartbeat ----------
    def send_heartbeat(self) -> None:
        if not self.mqtt_client or not self.gateway_sn:
            return
        topic = f"thing/product/{self.gateway_sn}/drc/down"
        payload = {"method": "heart_beat", "data": {"timestamp": _now_ms()}, "seq": _now_ms()}
        self.mqtt_client.publish(topic, json.dumps(payload), qos=0)

    # ---------- Phase B: joystick / movement ----------
    @staticmethod
    def _clamp(value, limit: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(-limit, min(limit, value))

    def update_joystick(self, x, y, h, w) -> None:
        """เรียกจาก ws_handler (asyncio thread) เมื่อ browser ส่ง joystick update มาใหม่"""
        with self._lock:
            self.joystick = {
                "x": self._clamp(x, XY_LIMIT),
                "y": self._clamp(y, XY_LIMIT),
                "h": self._clamp(h, H_LIMIT),
                "w": self._clamp(w, W_LIMIT),
            }
            self.joystick_updated_at = time.time()

    def publish_drone_control(self) -> None:
        """เรียกจาก control_tick_loop (asyncio) ทุก CONTROL_TICK_INTERVAL วินาที
        ระหว่างที่ drc_connected เท่านั้น — zero-velocity ถ้า joystick ไม่มีอัปเดตใหม่นานเกินไป"""
        if not self.mqtt_client or not self.gateway_sn:
            return

        with self._lock:
            stale = (time.time() - self.joystick_updated_at) > STALE_THRESHOLD
            values = dict(self.joystick) if not stale else {"x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0}

        # ส่งทุก tick เสมอตามที่เอกสาร DJI ระบุ (5-10Hz) แม้ค่าจะเป็น 0 — ไม่ข้าม
        # เพื่อไม่ให้ต้องเดาว่า flight controller ฝั่งโดรนจะตีความการ "เงียบ" อย่างไร
        self.control_seq += 1
        topic = f"thing/product/{self.gateway_sn}/drc/down"
        payload = {
            "method": "drone_control",
            "data": {"seq": self.control_seq, **values},
        }
        self.mqtt_client.publish(topic, json.dumps(payload), qos=0)

    def emergency_stop(self) -> None:
        """บายพาส tick loop ปกติ ยิงทันทีที่ได้รับคำสั่งจาก browser"""
        with self._lock:
            self.joystick = {"x": 0.0, "y": 0.0, "h": 0.0, "w": 0.0}
            self.joystick_updated_at = time.time()

        if self.mqtt_client and self.gateway_sn:
            topic = f"thing/product/{self.gateway_sn}/drc/down"
            payload = {"method": "drone_emergency_stop", "data": {}}
            self.mqtt_client.publish(topic, json.dumps(payload), qos=1)
            print(f"🛑 EMERGENCY STOP → {topic}")

        self.log_event("emergency_stop", "สั่งจากหน้าเว็บ")
        self.broadcast({"type": "ack", "method": "emergency_stop", "result": 0})

    # ---------- Phase C: gimbal (ก้ม-เงย) + camera aim (follow) ----------
    def _publish_payload_service(self, method: str, data: dict) -> None:
        """คำสั่งกล้อง/gimbal ส่งที่ topic .../services (ไม่ใช่ drc/down) ตาม DJI Cloud API"""
        if not self.mqtt_client or not self.gateway_sn:
            return
        data = {"payload_index": self.payload_index, **data}
        self._publish_service(method, data)

    def update_gimbal(self, pitch) -> None:
        """browser ส่ง rate ก้ม-เงย มาใหม่ — pitch หน่วย rad/s (บวก=เงยขึ้น, ลบ=ก้มลง)
        กล้อง M4E ไม่มีแกน yaw ให้คุม → ส่งแค่ pitch, locked=false → ตัวโดรนไม่ขยับ"""
        with self._lock:
            self.gimbal = {"pitch": self._clamp(pitch, GIMBAL_PITCH_LIMIT)}
            self.gimbal_updated_at = time.time()

    def publish_gimbal_drag(self) -> None:
        """เรียกจาก gimbal_tick_loop — ส่ง camera_screen_drag ต่อเนื่องระหว่างที่ยังสั่งค้าง
        และส่ง 0 หนึ่งครั้งเมื่อปล่อย/หมดอายุ (คำสั่ง rate ของ DJI ค้างจนกว่าจะส่ง 0)"""
        if not self.mqtt_client or not self.gateway_sn:
            return
        with self._lock:
            stale = (time.time() - self.gimbal_updated_at) > STALE_THRESHOLD
            pitch = 0.0 if stale else self.gimbal["pitch"]
            if pitch == 0.0 and self._last_gimbal_zero_sent:
                return
            self._last_gimbal_zero_sent = (pitch == 0.0)
        self._publish_payload_service("camera_screen_drag", {
            "locked": False,
            "pitch_speed": round(pitch, 3),
            "yaw_speed": 0.0,
        })

    def gimbal_reset(self) -> None:
        self._publish_payload_service("gimbal_reset", {"reset_mode": 0})
        self.log_event("gimbal_reset", "recenter จากหน้าเว็บ")

    def camera_aim(self, x, y, locked: bool = False) -> None:
        """เล็งกล้องไปที่พิกัดในเฟรม (0..1). ใช้ทั้งตอน tap-to-aim และตอน follow"""
        try:
            x = max(0.0, min(1.0, float(x)))
            y = max(0.0, min(1.0, float(y)))
        except (TypeError, ValueError):
            return
        now = time.time()
        if now - self.last_aim_at < CAMERA_AIM_MIN_INTERVAL:
            return
        self.last_aim_at = now
        self._publish_payload_service("camera_aim", {
            "camera_type": "zoom",
            "locked": bool(locked),
            "x": round(x, 4),
            "y": round(y, 4),
        })

    # ---------- gateway_sn discovery (จาก telemetry_listener ที่รันอยู่แล้ว) ----------
    def refresh_gateway_sn(self) -> None:
        # เรียกจาก thread ธรรมดา (ไม่ใช่ asyncio loop) เท่านั้น — ดู gateway_sn_poll_thread
        try:
            latest = TelemetryLog.objects.first()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ refresh_gateway_sn ล้มเหลว: {exc}")
            return
        if latest and latest.drone_sn and latest.drone_sn != self.gateway_sn:
            self.gateway_sn = latest.drone_sn
            print(f"🔗 gateway_sn = {self.gateway_sn} (จาก TelemetryLog ล่าสุด)")
            if self.control_state == "idle":
                self.set_status(message=f"พร้อมขอสิทธิ์ควบคุม gateway_sn={self.gateway_sn}")

    def _capture_payload_index(self, data: dict) -> None:
        """ดึง payload_index จริงของกล้องจาก OSD — ใช้เป็น target ของคำสั่ง gimbal/camera"""
        cameras = data.get("cameras")
        if isinstance(cameras, list) and cameras:
            pi = cameras[0].get("payload_index") or cameras[0].get("index")
            if pi and pi != self.payload_index:
                self.payload_index = pi
                print(f"📷 payload_index (จาก osd) = {pi}")

    # ---------- MQTT message handling ----------
    def handle_message(self, topic: str, payload: dict) -> None:
        method = payload.get("method", "")
        data = payload.get("data", {}) or {}

        if topic.endswith("/osd"):
            self._capture_payload_index(data)
            return

        if topic.endswith("/services_reply"):
            result = data.get("result")
            print(f"📩 services_reply [{method}] result={result}")
            if method == "cloud_control_auth_request":
                if result == 0:
                    self.log_event("granted")
                    self.set_status(control_state="granted", message="ได้รับสิทธิ์ควบคุมแล้ว — กำลังเข้า DRC mode")
                    self.enter_drc_mode()
                else:
                    self.log_event("denied", f"result={result}")
                    self.set_status(control_state="idle", message=f"ถูกปฏิเสธ/ล้มเหลว (result={result})")
            elif method == "drc_mode_enter":
                if result == 0:
                    self.set_status(message="drc_mode_enter สำเร็จ — รอยืนยันจาก drc/up traffic")
                else:
                    self.log_event("error", f"drc_mode_enter result={result}")
                    self.set_status(control_state="granted", message=f"drc_mode_enter ล้มเหลว (result={result})")
            return

        if topic.endswith("/events"):
            if method == "drc_status_notify":
                drc_state = data.get("drc_state", 0)
                print(f"📩 drc_status_notify drc_state={drc_state}")
                if drc_state == 2:
                    self.set_status(control_state="drc_connected", drc_state=2,
                                     message="เชื่อมต่อ DRC สำเร็จ — heartbeat กำลังทำงาน")
                elif drc_state == 1:
                    self.set_status(drc_state=1, message="กำลังเชื่อมต่อ DRC link...")
                else:
                    self.set_status(drc_state=0)
            elif method == "cloud_control_auth_notify":
                status = (data.get("output") or {}).get("status")
                print(f"📩 cloud_control_auth_notify status={status}")
                if status != "ok":
                    self.log_event("denied", f"auth_notify status={status}")
                    self.set_status(control_state="idle", message=f"คำขอถูกยกเลิก/ปฏิเสธ: {status}")
            return

        if topic.endswith("/state"):
            if "is_cloud_control_auth" in data:
                is_auth = data.get("is_cloud_control_auth")
                print(f"📩 state is_cloud_control_auth={is_auth}")
                if not is_auth and self.control_state in ("granted", "drc_connecting", "drc_connected"):
                    # ผู้บังคับที่ RC กดยึดสิทธิ์คืนเอง
                    self.log_event("watchdog_release", "RC ยึดสิทธิ์ควบคุมคืน")
                    self.set_status(control_state="idle", drc_state=0, message="RC ยึดสิทธิ์ควบคุมคืนแล้ว")
            return

        if topic.endswith("/drc/up"):
            # ยืนยันว่า DRC link ใช้งานได้จริงจาก "traffic" บน drc/up โดยตรง แทนที่จะรอ
            # drc_status_notify อย่างเดียว — ทดสอบกับ M4E จริงแล้วพบว่า drc_status_notify
            # ไม่เคยถูกส่งมาเลยแม้ว่า drc/up จะมีข้อมูล osd/battery/gimbal ไหลเข้ามาปกติทุกอย่าง
            if self.control_state == "drc_connecting":
                self.set_status(control_state="drc_connected", drc_state=2,
                                 message="เชื่อมต่อ DRC สำเร็จ (ยืนยันจาก drc/up traffic)")

            if method == "drone_control":
                result = data.get("result")
                if result:  # nonzero = error (ไม่มีสิทธิ์บิน/joystick/seq ผิด)
                    print(f"⚠️ drone_control result={result} (ไม่ใช่ 0 = มีปัญหา)")
                    self.broadcast({"type": "ack", "method": "drone_control", "result": result})
                return
            if method == "hsi_info_push":
                self.broadcast({"type": "obstacle", **data})
                return
            if method in ("camera_screen_drag", "camera_aim", "gimbal_reset"):
                result = data.get("result")
                if result:
                    print(f"⚠️ {method} result={result}")
                self.broadcast({"type": "ack", "method": method, "result": result or 0})
                return
            if method in ("drc_camera_osd_info_push", "camera_osd_info_push"):
                pi = data.get("payload_index")
                if pi and pi != self.payload_index:
                    self.payload_index = pi
                    print(f"📷 payload_index (จาก drc/up) = {pi}")
                return
            # method อื่นๆ (osd_info_push, drc_battery/geo/heart_beat ฯลฯ)
            # ยิงถี่มาก (หลายครั้ง/วิ) — ไม่ log raw ทุกตัวเพื่อไม่ให้ log ท่วม
            return


controller = DrcController()


# ---------------- MQTT callbacks (รันใน paho thread) ----------------

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or str(reason_code) == "Success":
        print("✅ เชื่อมต่อ MQTT Broker (EMQX) สำเร็จ [drc_controller]")
        client.subscribe("thing/product/+/services_reply", qos=1)
        client.subscribe("thing/product/+/events", qos=1)
        client.subscribe("thing/product/+/state", qos=1)
        client.subscribe("thing/product/+/drc/up", qos=0)
        client.subscribe("thing/product/+/osd", qos=0)
        print("📡 Subscribe: services_reply, events, state, drc/up, osd")
    else:
        print(f"❌ เชื่อมต่อล้มเหลว: {reason_code}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    controller.handle_message(msg.topic, payload)


# ---------------- asyncio / WebSocket ----------------

async def ws_handler(ws):
    controller.ws_clients.add(ws)
    print(f"🔌 WS client เชื่อมต่อ ({len(controller.ws_clients)} ราย)")
    try:
        await ws.send(json.dumps({
            "type": "status",
            "control_state": controller.control_state,
            "drc_state": controller.drc_state,
            "gateway_sn": controller.gateway_sn,
            "message": controller.last_message,
        }))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type == "request_control":
                controller.request_control()
            elif msg_type == "release_control":
                controller.release_control("released", "สั่งปล่อยจากหน้าเว็บ")
            elif msg_type == "joystick":
                if controller.control_state == "drc_connected":
                    controller.update_joystick(
                        msg.get("x", 0), msg.get("y", 0), msg.get("h", 0), msg.get("w", 0)
                    )
            elif msg_type == "emergency_stop":
                controller.emergency_stop()
            elif msg_type == "gimbal":
                # ควบคุมกล้อง ก้ม-เงย — ใช้ได้เมื่อ drc_connected (locked=false → โดรนไม่ขยับ)
                if controller.control_state == "drc_connected":
                    controller.update_gimbal(msg.get("pitch", 0))
            elif msg_type == "gimbal_reset":
                if controller.control_state == "drc_connected":
                    controller.gimbal_reset()
            elif msg_type == "camera_aim":
                # tap-to-aim หรือ follow: เล็งกล้องไปที่จุด (x,y) 0..1 ในเฟรม
                if controller.control_state == "drc_connected":
                    controller.camera_aim(msg.get("x"), msg.get("y"), locked=bool(msg.get("locked", False)))
            elif msg_type == "follow":
                controller.follow_active = bool(msg.get("active"))
                controller.broadcast({"type": "follow", "active": controller.follow_active})
            else:
                await ws.send(json.dumps({"type": "ack", "method": msg_type, "result": -1,
                                           "message": "ไม่รู้จักคำสั่งนี้"}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        controller.ws_clients.discard(ws)
        print(f"🔌 WS client หลุด ({len(controller.ws_clients)} ราย เหลือ)")
        if not controller.ws_clients:
            controller.last_ws_disconnect_at = time.time()


async def disconnect_watchdog_loop():
    """ถ้าไม่มี browser ต่ออยู่เลยเกิน grace period ระหว่างที่ยังถือสิทธิ์ควบคุม → ปล่อยอัตโนมัติ"""
    while True:
        await asyncio.sleep(0.5)
        if (
            not controller.ws_clients
            and controller.last_ws_disconnect_at is not None
            and controller.control_state in ("granted", "drc_connecting", "drc_connected")
            and time.time() - controller.last_ws_disconnect_at > DISCONNECT_GRACE_SECONDS
        ):
            print("⏱️ ไม่มี browser เชื่อมต่อเกิน grace period — ปล่อยสิทธิ์ควบคุมอัตโนมัติ")
            controller.release_control("watchdog_release", "ไม่มี browser เชื่อมต่อเกิน grace period")
            controller.last_ws_disconnect_at = None


async def heartbeat_loop():
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        if controller.control_state in ("drc_connecting", "drc_connected"):
            controller.send_heartbeat()


async def control_tick_loop():
    """ยิง drone_control ที่ CONTROL_TICK_INTERVAL วินาที (คงที่ ≤10Hz) ตลอดเวลาที่ drc_connected เท่านั้น"""
    while True:
        await asyncio.sleep(CONTROL_TICK_INTERVAL)
        if controller.control_state == "drc_connected":
            controller.publish_drone_control()


async def gimbal_tick_loop():
    """ยิง camera_screen_drag ต่อเนื่องระหว่างที่กล้องกำลังถูกสั่งค้าง + ส่ง 0 เมื่อปล่อย"""
    while True:
        await asyncio.sleep(GIMBAL_TICK_INTERVAL)
        if controller.control_state == "drc_connected":
            controller.publish_gimbal_drag()


def gateway_sn_poll_thread():
    """รันใน thread ธรรมดา (ไม่ใช่ asyncio) เพราะ Django ORM sync calls ห้ามเรียกตรงจาก event loop thread"""
    while True:
        controller.refresh_gateway_sn()
        time.sleep(1.0)


_shutdown_started = False


def shutdown_cleanup():
    """พยายามปล่อยสิทธิ์ควบคุมก่อนปิดตัว — ครอบคลุม SIGTERM/exit ปกติ (ไม่ครอบคลุม SIGKILL/crash แรง)"""
    global _shutdown_started
    if _shutdown_started:
        return
    _shutdown_started = True
    # กันค้าง: ถ้า cleanup ไม่จบใน 3 วิ (เช่น MQTT publish qos=1 บล็อกเพราะ broker หลุด) → บังคับออก
    threading.Timer(3.0, lambda: os._exit(0)).start()

    if controller.control_state in ("granted", "drc_connecting", "drc_connected"):
        print("🛑 กำลังปิดตัว — ปล่อยสิทธิ์ควบคุมก่อน...")
        try:
            controller.release_control("watchdog_release", "service กำลังปิดตัว (shutdown)")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ ปล่อยสิทธิ์ตอนปิดตัวล้มเหลว: {exc}")
    if controller.mqtt_client:
        controller.mqtt_client.loop_stop()
        controller.mqtt_client.disconnect()


async def main_async():
    controller.loop = asyncio.get_running_loop()

    def _on_sigterm(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    threading.Thread(target=gateway_sn_poll_thread, daemon=True).start()

    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        print(f"🌐 DRC WebSocket → 0.0.0.0:{WS_PORT}")
        await asyncio.gather(
            heartbeat_loop(),
            disconnect_watchdog_loop(),
            control_tick_loop(),
            gimbal_tick_loop(),
        )


def main():
    print(f"🔌 MQTT {MQTT_HOST}:{MQTT_PORT} user={DRC_MQTT_USER} [drc_controller]")
    if not DRC_MQTT_PASSWORD:
        print("⚠️ DRC_MQTT_PASSWORD ว่างเปล่า — ตั้งค่าใน .env และสร้าง user นี้ใน EMQX ก่อนใช้งานจริง")

    # client_id ไม่ซ้ำต่อ process — กัน "takeover war" ถ้ามี instance เก่าค้าง (EMQX เตะตัวที่ client_id ซ้ำ)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"drc_controller_{os.getpid()}")
    client.username_pw_set(DRC_MQTT_USER, DRC_MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    controller.mqtt_client = client

    atexit.register(shutdown_cleanup)

    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 ปิด DRC Controller")
    finally:
        shutdown_cleanup()


if __name__ == "__main__":
    main()
