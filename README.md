# DJI Matrice 4E — Telemetry & AI Dashboard

Prototype ที่เชื่อม DJI Pilot 2 (Cloud API) เข้ากับ Ubuntu Server ผ่าน MQTT (พิกัด) และ RTMP (วิดีโอ)

## Quick Start

```bash
# 1. ติดตั้ง dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. ตั้งค่า .env (แก้ SERVER_IP, DJI_WORKSPACE_ID ให้ตรงกับ DJI Portal)
cp .env.example .env   # หรือแก้ .env ที่มีอยู่

# 3. รัน EMQX + SRS และ migrate DB
chmod +x scripts/setup.sh
./scripts/setup.sh

# 4. รัน Django (พอร์ต 8006 — 8000 ถูก WebODM ใช้อยู่)
cd drone_backend
../.venv/bin/python manage.py runserver 0.0.0.0:8006
```

## การเชื่อม DJI Pilot 2

1. ใน **DJI Developer Portal** ตั้ง H5 URL:
   ```
   http://<SERVER_IP>:8006/dji/login/
   ```
2. ตั้ง `DJI_WORKSPACE_ID` ใน `.env` ให้ตรงกับ Workspace ใน Portal
3. บน **RC Plus 2** → DJI Pilot 2 → Cloud Service → ใส่ URL ด้านบน
4. กด **ยืนยันการเชื่อมต่อเซิร์ฟเวอร์** ในหน้า H5
5. เปิด Mission Control → กด **เปิด Telemetry**

## URLs

| หน้า | URL |
|------|-----|
| H5 Handshake (Pilot 2) | `http://<IP>:8006/dji/login/` |
| Mission Control | `http://<IP>:8006/dashboard/mission/` |
| EMQX Dashboard | `http://<IP>:18083` (admin / admin1234) |

## Data Flow

- **พิกัด:** Pilot 2 → MQTT `thing/product/+/osd` → `telemetry_listener.py` → SQLite → `/api/drone-location/`
- **วิดีโอ:** Pilot 2 → RTMP `rtmp://<IP>:1935/live/drone` → SRS → FLV `http://<IP>:8085/live/drone.flv`

## Troubleshooting

- แผนที่ไม่ขยับ → ตรวจ EMQX Dashboard ว่ามี client publish ที่ topic `thing/product/*/osd`
- ไม่มีวิดีโอ → ต้อง handshake H5 ใน Pilot 2 ก่อน (ระบบจะตั้ง RTMP อัตโนมัติ)
- ดู log: `logs/telemetry_listener.log`, `logs/video_processor.log`
