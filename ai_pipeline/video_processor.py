import os
import sys
import time

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

SERVER_IP = os.getenv("SERVER_IP", "127.0.0.1")
RTMP_PORT = os.getenv("RTMP_PORT", "1935")
RTMP_APP = os.getenv("RTMP_APP", "live")
RTMP_STREAM_KEY = os.getenv("RTMP_STREAM_KEY", "drone")
RTMP_URL = f"rtmp://127.0.0.1:{RTMP_PORT}/{RTMP_APP}/{RTMP_STREAM_KEY}"
TARGET_CLASSES = [0, 2]
RETRY_SECONDS = 5


def open_stream():
    cap = cv2.VideoCapture(RTMP_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main():
    print("🧠 กำลังโหลด YOLOv8...")
    model = YOLO("yolov8n.pt")
    print(f"📹 รอ RTMP stream: {RTMP_URL}")

    cap = None
    frame_count = 0

    while True:
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            print(f"⏳ รอสัญญาณ RTMP จาก Pilot 2 ({RTMP_URL})...")
            cap = open_stream()
            if not cap.isOpened():
                time.sleep(RETRY_SECONDS)
                continue
            print("✅ เปิด RTMP stream สำเร็จ")

        ret, frame = cap.read()
        if not ret:
            print("⚠️ อ่านเฟรมไม่ได้ — reconnect")
            cap.release()
            cap = None
            time.sleep(RETRY_SECONDS)
            continue

        frame_count += 1
        if frame_count % 5 != 0:
            continue

        results = model.predict(frame, verbose=False, classes=TARGET_CLASSES)[0]
        latest = TelemetryLog.objects.first()
        current_lat = latest.latitude if latest else 0.0
        current_lng = latest.longitude if latest else 0.0

        for box in results.boxes:
            class_id = int(box.cls[0])
            confidence = float(box.conf[0]) * 100
            if confidence <= 50:
                continue

            obj_type = "person" if class_id == 0 else "car"
            DetectionLog.objects.create(
                object_type=obj_type,
                confidence=round(confidence, 2),
                latitude=current_lat,
                longitude=current_lng,
            )
            print(f"🎯 {obj_type} {round(confidence, 1)}% @ {current_lat}, {current_lng}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 ปิด AI Video Processor")
