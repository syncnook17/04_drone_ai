"""Supervisor สำหรับ video_processor.py

video_processor พึ่ง OpenCV/ffmpeg ซึ่ง "segfault" ได้ตอนสตรีมโดรน corrupt หนัก
(uplink อ่อน / bitrate ต่ำมาก) — Python catch ไม่ได้. ตัวนี้รัน video_processor เป็น
subprocess แล้วรีสตาร์ทให้อัตโนมัติเมื่อมันตาย (มี exponential backoff กันรีสตาร์ทรัวๆ)

service_manager ชี้ pid มาที่ตัวนี้ — ส่ง SIGTERM มาจะ kill ทั้ง subprocess แล้วออก
"""
import os
import signal
import subprocess
import sys
import time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(CURRENT_DIR, "video_processor.py")
PYTHON = sys.executable

_child: subprocess.Popen | None = None


def _terminate(signum, frame):
    global _child
    if _child and _child.poll() is None:
        _child.terminate()
        try:
            _child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _child.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, _terminate)
signal.signal(signal.SIGINT, _terminate)


def main() -> None:
    global _child
    backoff = 2
    while True:
        started = time.time()
        print(f"▶️  [supervisor] เริ่ม video_processor (python={PYTHON})", flush=True)
        _child = subprocess.Popen([PYTHON, TARGET], env={**os.environ, "PYTHONUNBUFFERED": "1"})
        rc = _child.wait()
        ran_for = time.time() - started
        print(f"⚠️  [supervisor] video_processor ออก (rc={rc}) หลังทำงาน {ran_for:.0f}s", flush=True)

        # ถ้าอยู่ได้นานพอ ถือว่าเสถียร → รีเซ็ต backoff
        backoff = 2 if ran_for > 60 else min(backoff * 2, 30)
        print(f"⏳ [supervisor] รีสตาร์ทใน {backoff}s", flush=True)
        time.sleep(backoff)


if __name__ == "__main__":
    main()
