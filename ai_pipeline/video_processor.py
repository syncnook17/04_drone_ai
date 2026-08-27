import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
from dotenv import load_dotenv
from ultralytics import YOLO

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

sys.path.append(os.path.join(ROOT_DIR, "drone_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "drone_system.settings")

import django

django.setup()

from app_core.models import DetectionLog, TelemetryLog

RTMP_PORT = os.getenv("RTMP_PORT", "1935")
RTMP_APP = os.getenv("RTMP_APP", "live")
RTMP_STREAM_KEY = os.getenv("RTMP_STREAM_KEY", "drone")
RTMP_URL = f"rtmp://127.0.0.1:{RTMP_PORT}/{RTMP_APP}/{RTMP_STREAM_KEY}"
# ทางเลือก: อ่านผ่าน FLV ของ SRS (AI_INGEST=flv) — ดีฟอลต์ใช้ RTMP ตรงเหมือนเดิม
SRS_HTTP_PORT = os.getenv("SRS_HTTP_PORT", "8085")
FLV_URL = f"http://127.0.0.1:{SRS_HTTP_PORT}/{RTMP_APP}/{RTMP_STREAM_KEY}.flv"
INGEST_URL = FLV_URL if os.getenv("AI_INGEST", "rtmp") == "flv" else RTMP_URL
# low-latency flags — ไม่ใส่ key 'timeout' (สำหรับ rtmp มันหมายถึง listen mode)
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay"
)
TARGET_CLASSES = [0, 2]  # person, car
MIN_CONFIDENCE = float(os.getenv("AI_MIN_CONFIDENCE", "28"))
IMGSZ = int(os.getenv("AI_IMGSZ", "640"))
INFERENCE_MAX_WIDTH = int(os.getenv("AI_INFERENCE_MAX_WIDTH", "1280"))
SKIP_READ = int(os.getenv("AI_SKIP_READ", "3"))
MODEL_NAME = os.getenv("AI_MODEL", "yolov8s.pt")
MAX_DET = int(os.getenv("AI_MAX_DET", "16"))
MIN_BOX_PX = int(os.getenv("AI_MIN_BOX_PX", "10"))  # อนุญาตกล่องเล็กสำหรับมุมสูง
RETRY_SECONDS = 2
DETECTIONS_JSON = os.path.join(ROOT_DIR, "run", "latest_detections.json")
LOG_DB_EVERY_N = 90
MJPEG_PORT = int(os.getenv("AI_MJPEG_PORT", "8009"))
MJPEG_QUALITY = int(os.getenv("AI_MJPEG_QUALITY", "65"))
MJPEG_MAX_WIDTH = int(os.getenv("AI_MJPEG_MAX_WIDTH", "960"))
MJPEG_MAX_FPS = float(os.getenv("AI_MJPEG_MAX_FPS", "15"))
MJPEG_MIN_INTERVAL = 1.0 / MJPEG_MAX_FPS if MJPEG_MAX_FPS > 0 else 0.0
BOX_COLOR = (68, 68, 239)  # BGR, สีเดียวกับกรอบแดงที่ dashboard เคยวาด

USE_HALF = False
try:
    import torch

    USE_HALF = torch.cuda.is_available()
except ImportError:
    pass


def open_stream():
    cap = cv2.VideoCapture(INGEST_URL, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def resize_for_inference(frame):
    height, width = frame.shape[:2]
    if width <= INFERENCE_MAX_WIDTH:
        return frame, 1.0
    scale = INFERENCE_MAX_WIDTH / width
    resized = cv2.resize(frame, (int(width * scale), int(height * scale)))
    return resized, scale


def match_boxes(prev_boxes: list[dict], curr_boxes: list[dict]) -> list[tuple[dict | None, dict]]:
    if not prev_boxes:
        return [(None, box) for box in curr_boxes]

    used = set()
    pairs = []
    for curr in curr_boxes:
        cx = curr["x"] + curr["w"] / 2
        cy = curr["y"] + curr["h"] / 2
        best_idx = None
        best_dist = float("inf")
        for idx, prev in enumerate(prev_boxes):
            if idx in used or prev.get("label") != curr.get("label"):
                continue
            px = prev["x"] + prev["w"] / 2
            py = prev["y"] + prev["h"] / 2
            dist = (cx - px) ** 2 + (cy - py) ** 2
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx is not None and best_dist < 120 ** 2:
            used.add(best_idx)
            pairs.append((prev_boxes[best_idx], curr))
        else:
            pairs.append((None, curr))
    return pairs


def boxes_from_results(results, scale: float, prev_boxes: list[dict], dt: float) -> list[dict]:
    raw_boxes = []
    for box in results.boxes:
        class_id = int(box.cls[0])
        confidence = float(box.conf[0]) * 100
        if confidence < MIN_CONFIDENCE:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        inv = 1.0 / scale
        bw = (x2 - x1) * inv
        bh = (y2 - y1) * inv
        if bw < MIN_BOX_PX or bh < MIN_BOX_PX:
            continue
        obj_type = "person" if class_id == 0 else "car"
        raw_boxes.append(
            {
                "x": round(x1 * inv, 1),
                "y": round(y1 * inv, 1),
                "w": round(bw, 1),
                "h": round(bh, 1),
                "label": obj_type,
                "confidence": round(confidence, 1),
            }
        )

    dt = max(dt, 0.016)
    boxes = []
    for prev, curr in match_boxes(prev_boxes, raw_boxes):
        entry = dict(curr)
        if prev:
            entry["vx"] = round((curr["x"] - prev["x"]) / dt, 1)
            entry["vy"] = round((curr["y"] - prev["y"]) / dt, 1)
        else:
            entry["vx"] = 0.0
            entry["vy"] = 0.0
        boxes.append(entry)
    return boxes


def write_latest_detections(frame, boxes, seq: int, infer_ms: float) -> None:
    height, width = frame.shape[:2]
    now = time.time()
    payload = {
        "updated_at": now,
        "seq": seq,
        "frame_width": width,
        "frame_height": height,
        "infer_ms": round(infer_ms, 1),
        "boxes": boxes,
    }
    os.makedirs(os.path.dirname(DETECTIONS_JSON), exist_ok=True)
    tmp_path = DETECTIONS_JSON + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp_path, DETECTIONS_JSON)


def draw_boxes_on_frame(frame, boxes) -> None:
    """วาดกรอบ detection ลงในเฟรมจริงก่อนส่ง MJPEG — เพื่อให้ภาพกับกรอบตรงกันเสมอ"""
    for box in boxes:
        x, y, w, h = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
        label = f'{box["label"]} {box["confidence"]}%'
        cv2.rectangle(frame, (x, y), (x + w, y + h), BOX_COLOR, 2)
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        label_y = max(0, y - text_h - 8)
        cv2.rectangle(frame, (x, label_y), (x + text_w + 6, y), BOX_COLOR, -1)
        cv2.putText(
            frame, label, (x + 3, max(text_h + 2, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA,
        )


class MjpegStream:
    """เก็บเฟรมล่าสุด (JPEG bytes) ให้ handler หลาย client อ่านพร้อมกันได้
    ลดขนาด + จำกัด fps ก่อนเข้ารหัส เพื่อไม่ให้กิน bandwidth มากเกินไปตอนดูข้าม internet"""

    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg_bytes: bytes | None = None
        self.last_publish_at = 0.0

    def publish(self, frame) -> None:
        now = time.time()
        if now - self.last_publish_at < MJPEG_MIN_INTERVAL:
            return
        self.last_publish_at = now

        height, width = frame.shape[:2]
        if width > MJPEG_MAX_WIDTH:
            scale = MJPEG_MAX_WIDTH / width
            frame = cv2.resize(frame, (MJPEG_MAX_WIDTH, int(height * scale)))

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, MJPEG_QUALITY])
        if not ok:
            return
        with self.lock:
            self.jpeg_bytes = buf.tobytes()

    def latest(self) -> bytes | None:
        with self.lock:
            return self.jpeg_bytes


mjpeg_stream = MjpegStream()


class MjpegHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") != "/mjpeg":
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()

        last_sent = None
        try:
            while True:
                jpeg_bytes = mjpeg_stream.latest()
                if jpeg_bytes is None or jpeg_bytes is last_sent:
                    time.sleep(0.02)
                    continue
                last_sent = jpeg_bytes
                self.wfile.write(b"--FRAME\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg_bytes)}\r\n\r\n".encode())
                self.wfile.write(jpeg_bytes)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


class MjpegServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_mjpeg_server() -> None:
    server = MjpegServer(("0.0.0.0", MJPEG_PORT), MjpegHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"🎥 MJPEG (กรอบฝังในภาพ) → 0.0.0.0:{MJPEG_PORT}/mjpeg")


def maybe_log_to_db(boxes) -> None:
    latest = TelemetryLog.objects.first()
    current_lat = latest.latitude if latest else 0.0
    current_lng = latest.longitude if latest else 0.0
    for box in boxes:
        DetectionLog.objects.create(
            object_type=box["label"],
            confidence=box["confidence"],
            latitude=current_lat,
            longitude=current_lng,
        )


class FrameGrabber(threading.Thread):
    """อ่าน RTMP ใน thread เดียว — OpenCV VideoCapture ไม่ thread-safe"""

    def __init__(self):
        super().__init__(daemon=True)
        self.cap_lock = threading.Lock()
        self.frame_lock = threading.Lock()
        self.cap = None
        self.latest_frame = None
        self.frame_id = 0
        self.running = True
        self.stream_ok = False

    def set_cap(self, cap):
        with self.cap_lock:
            if self.cap is not None:
                self.cap.release()
            self.cap = cap

    def run(self):
        while self.running:
            with self.cap_lock:
                cap = self.cap

            if cap is None or not cap.isOpened():
                self.stream_ok = False
                time.sleep(0.05)
                continue

            latest = None
            for _ in range(SKIP_READ + 1):
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                latest = frame

            if latest is None:
                self.stream_ok = False
                time.sleep(0.02)
                continue

            self.stream_ok = True
            with self.frame_lock:
                self.latest_frame = latest
                self.frame_id += 1

    def take_latest(self):
        with self.frame_lock:
            if self.latest_frame is None:
                return None, 0
            return self.latest_frame.copy(), self.frame_id

    def stop(self):
        self.running = False


class MjpegPublisher(threading.Thread):
    """ส่งภาพออก MJPEG แยก thread — FPS ไม่ผูกกับความเร็ว inference
    ดึงเฟรมดิบล่าสุดจาก grabber แล้ววาดกรอบ 'ล่าสุดที่ AI เจอ' ทับ (extrapolate ด้วย vx/vy)
    ทำให้ภาพลื่นแม้ inference จะช้า/สะดุด"""

    def __init__(self, grabber: "FrameGrabber"):
        super().__init__(daemon=True)
        self.grabber = grabber
        self.running = True
        self._boxes: list[dict] = []
        self._boxes_at = 0.0
        self._lock = threading.Lock()

    def update_boxes(self, boxes: list[dict]) -> None:
        with self._lock:
            self._boxes = boxes
            self._boxes_at = time.time()

    def _current_boxes(self):
        with self._lock:
            dt = time.time() - self._boxes_at
            if dt > 1.5:  # เก่าเกินไป — ไม่วาด (กันกรอบค้างตอน AI หลุด)
                return []
            out = []
            for b in self._boxes:
                nb = dict(b)
                nb["x"] = b["x"] + b.get("vx", 0.0) * dt
                nb["y"] = b["y"] + b.get("vy", 0.0) * dt
                out.append(nb)
            return out

    def run(self):
        last_id = -1
        while self.running:
            frame, frame_id = self.grabber.take_latest()
            if frame is None or frame_id == last_id:
                time.sleep(MJPEG_MIN_INTERVAL / 2 if MJPEG_MIN_INTERVAL else 0.01)
                continue
            last_id = frame_id
            draw_boxes_on_frame(frame, self._current_boxes())
            mjpeg_stream.publish(frame)


def main():
    print("🧠 กำลังโหลด YOLOv8...")
    model = YOLO(MODEL_NAME)
    print(f"📹 Ingest: {INGEST_URL}")
    print(f"📦 Overlay JSON → {DETECTIONS_JSON}")
    print(
        f"⚡ model={MODEL_NAME}, imgsz={IMGSZ}, "
        f"max_w={INFERENCE_MAX_WIDTH}, conf={MIN_CONFIDENCE}%, half={USE_HALF}"
    )

    start_mjpeg_server()

    grabber = FrameGrabber()
    grabber.start()

    publisher = MjpegPublisher(grabber)
    publisher.start()
    print(f"🖼️  MJPEG publisher แยก thread (สูงสุด {MJPEG_MAX_FPS} fps, ไม่ผูกกับ inference)")

    seq = 0
    prev_boxes: list[dict] = []
    last_processed_id = 0
    last_infer_at = time.time()
    cap = None

    while True:
        if cap is None or not cap.isOpened() or not grabber.stream_ok:
            if cap is not None:
                grabber.set_cap(None)
                cap.release()
                cap = None
            print(f"⏳ รอสัญญาณวิดีโอ ({INGEST_URL})...")
            cap = open_stream()
            if not cap.isOpened():
                time.sleep(RETRY_SECONDS)
                continue
            grabber.set_cap(cap)
            print("✅ เปิด RTMP stream สำเร็จ")
            time.sleep(0.3)

        frame, frame_id = grabber.take_latest()
        if frame is None or frame_id == last_processed_id:
            time.sleep(0.003)
            continue

        last_processed_id = frame_id
        try:
            infer_frame, scale = resize_for_inference(frame)
            t0 = time.perf_counter()
            results = model.predict(
                infer_frame,
                classes=TARGET_CLASSES,
                imgsz=IMGSZ,
                conf=MIN_CONFIDENCE / 100,
                verbose=False,
                max_det=MAX_DET,
                half=USE_HALF,
            )[0]
            infer_ms = (time.perf_counter() - t0) * 1000
            now = time.time()
            dt = now - last_infer_at
            last_infer_at = now

            boxes = boxes_from_results(results, scale, prev_boxes, dt)
            prev_boxes = boxes
            seq += 1
            write_latest_detections(frame, boxes, seq, infer_ms)

            # ส่งกรอบให้ publisher thread ไปวาดทับเฟรมดิบล่าสุด (ไม่วาด/ส่งเองแล้ว)
            publisher.update_boxes(boxes)

            if seq % LOG_DB_EVERY_N == 0 and boxes:
                maybe_log_to_db(boxes)
        except Exception as exc:  # noqa: BLE001 - เฟรมเสีย/inference พลาด ไม่ควรทำให้ service ตาย
            print(f"⚠️ ประมวลผลเฟรมล้มเหลว (ข้าม): {exc}")
            time.sleep(0.05)


if __name__ == "__main__":
    # supervisor: ถ้า main() พังจาก exception (เช่น decode error สะสม) ให้เริ่มใหม่เอง
    # (segfault ระดับ ffmpeg native ยังต้องพึ่ง service_manager/systemd ข้างนอก)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("\n🛑 ปิด AI Video Processor")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"💥 main() ล้มเหลว: {exc!r} — เริ่มใหม่ใน 3 วิ")
            time.sleep(3)
