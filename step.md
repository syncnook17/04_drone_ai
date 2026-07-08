# คู่มือเชื่อมต่อ DJI Cloud — Telemetry + Video

โปรเจกต์นี้เชื่อม **DJI Pilot 2 บน RC Plus 2** กับ Ubuntu Server เพื่อรับ **พิกัด GPS (MQTT)** และ **วิดีโอสด (RTMP)** แสดงบน Dashboard

อ้างอิงมาตรฐาน: [DJI Cloud API Demo](https://github.com/dji-sdk/Cloud-API-Demo-Web) · [DJI Cloud API Doc (MQTT Topics)](https://github.com/dji-sdk/Cloud-API-Doc/blob/master/docs/en/60.api-reference/10.pilot-to-cloud/00.mqtt/00.topic-definition.md)

---

## 1. สถาปัตยกรรมโดยรวม

```
RC Plus 2 (Pilot 2)
  │
  ├─ H5 Handshake ──► http://SERVER_IP:8007/dji/login/
  │                      └─ JSBridge: License → Workspace → Thing(MQTT) → Liveshare(RTMP)
  │
  ├─ MQTT Telemetry ──► tcp://SERVER_IP:1883  (EMQX)
  │                      ├─ sys/product/{RC_SN}/status        (update_topo)
  │                      ├─ sys/product/{RC_SN}/status_reply  (server ต้องตอบ!)
  │                      └─ thing/product/{DRONE_SN}/osd        (lat/lng/height ทุก ~2 วิ)
  │
  ├─ RTMP Video ────────► rtmp://SERVER_IP:1935/live/drone  (SRS)
  │                      └─ ดูบนเว็บ: http://SERVER_IP:8085/live/drone.flv
  │
  └─ Dashboard ─────────► http://SERVER_IP:8007/dashboard/mission/
                             └─ nginx :8007 → Django :8008
```

| พอร์ต | บริการ | ใครใช้ |
|------|--------|--------|
| **8007** | nginx gateway (HTTP + MQTT WS `/mqtt`) | RC เปิด browser |
| **8008** | Django (internal) | nginx proxy เท่านั้น |
| **1883** | EMQX MQTT TCP | Pilot Thing module, telemetry_listener |
| **8006** | EMQX MQTT (mapped จาก 1883) | ทางเลือกสำหรับ RC ถ้า 1883 ถูกบล็อก |
| **8083** | EMQX MQTT WebSocket | ทดสอบ browser / nginx proxy |
| **18083** | EMQX Dashboard | admin ดู clients/topics |
| **1935** | SRS RTMP ingest | Pilot Liveshare |
| **8085** | SRS HTTP-FLV | Dashboard แสดงวิดีโอ |

---

## 2. ไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
|------|---------|
| `.env` | IP, credentials, พอร์ตทั้งหมด |
| `docker-compose.yml` | EMQX, SRS, nginx gateway |
| `nginx/drone-gateway.conf` | proxy `:8007` → Django `:8008` + MQTT WS |
| `emqx/auth-built-in-db.csv` | user MQTT: `pilot/pilot123`, `admin/admin1234` |
| `drone_backend/templates/app_core/h5_login.html` | หน้า Handshake บน Pilot |
| `drone_backend/app_core/views.py` | API login, MQTT config, livestream config |
| `ai_pipeline/telemetry_listener.py` | รับ MQTT → ตอบ status_reply → บันทึกพิกัด DB |
| `drone_backend/templates/app_core/mission_control.html` | Dashboard แผนที่ + วิดีโอ |

---

## 3. ขั้นตอนเปิดระบบ (ทุกครั้งที่ reboot server)

### 3.1 เปิด Docker services

```bash
cd /home/ddm/02_drone_ai
docker compose up -d
```

ตรวจว่า 3 container ขึ้น: `dji_mqtt_emqx`, `dji_rtmp_srs`, `dji_gateway`

### 3.2 เปิด Django (ถ้าไม่เปิดจะได้ 502 Bad Gateway)

```bash
cd /home/ddm/02_drone_ai/drone_backend
nohup ../.venv/bin/python manage.py runserver 0.0.0.0:8008 >> ../logs/django.log 2>&1 &
```

ทดสอบ: เปิด `http://192.168.1.120:8007/dashboard/mission/` ต้องไม่ขึ้น 502

### 3.3 เปิด Telemetry Listener

```bash
cd /home/ddm/02_drone_ai/ai_pipeline
python -u telemetry_listener.py
```

ต้องเห็น:
```
✅ Subscribe OK (mid=1..3)
ℹ️  รอ update_topo จาก RC → ตอบ status_reply → รับ OSD พิกัด
```

> **สำคัญ:** listener ต้องรันอยู่ตลอดที่ต้องการรับพิกัด — มันไม่ได้ auto-start หลัง reboot

---

## 4. ขั้นตอนบน RC Plus 2 (Pilot 2)

### 4.1 ตั้งค่า Cloud Service ใน Pilot 2

1. เปิด **DJI Pilot 2**
2. ไป **Cloud Service** → ใส่ URL:
   ```
   http://192.168.1.120:8007/dji/login/
   ```
3. กดปุ่ม **เริ่มการเชื่อมต่อระบบแบบ One-Click**

### 4.2 ลำดับ Handshake (ใน H5)

| ลำดับ | ขั้นตอน | JSBridge / API |
|------|---------|----------------|
| 1 | ตรวจ License | `platformVerifyLicense(appId, appKey, appLicense)` |
| 2 | ตั้ง Workspace | `platformSetWorkspaceId(workspaceId)` |
| 3 | เชื่อม MQTT | `platformLoadComponent("thing", {...})` |
| 4 | เปิด Liveshare | `platformLoadComponent("liveshare", {...})` |
| 5 | ตั้ง RTMP URL | `liveshareSetConfig(2, JSON.stringify({url}))` |
| 6 | เริ่มสตรีม | `liveshareStartLive()` |

### 4.3 Code สำคัญใน H5 (`h5_login.html`)

**MQTT Thing module — ใช้ TCP ไม่ใช่ WebSocket**

```javascript
let mqttHostUrl = "tcp://" + serverIp + ":1883";

let thingParam = {
    host: mqttHostUrl,
    username: "admin",
    password: "admin1234",
    connectCallback: "thingConnectCallback"   // ต้องประกาศ window.thingConnectCallback ไว้ก่อน
};
window.djiBridge.platformLoadComponent("thing", JSON.stringify(thingParam));
```

**Liveshare + RTMP**

```javascript
window.djiBridge.platformLoadComponent("liveshare", JSON.stringify({
    videoPublishType: "video-by-manual",
    statusCallback: "liveStatusCallback"
}));
window.djiBridge.liveshareSetVideoPublishType("video-by-manual");

let rtmpTargetUrl = "rtmp://" + serverIp + ":1935/live/drone";
window.djiBridge.liveshareSetConfig(2, JSON.stringify({ url: rtmpTargetUrl }));
window.djiBridge.liveshareStartLive();
```

**Callback ที่ต้องประกาศไว้ใน global scope**

```javascript
window.thingConnectCallback = function(resRaw) { /* MQTT สำเร็จ/ล้มเหลว */ };
window.liveStatusCallback = function(resRaw) { /* สถานะวิดีโอ */ };
```

---

## 5. Telemetry (พิกัด GPS) — ขั้นตอนที่สำคัญที่สุด

### 5.1 Flow ข้อมูล

```
Pilot MQTT connect (Thing)
    ↓
RC ส่ง sys/product/{RC_SN}/status  method=update_topo
    ↓
telemetry_listener ตอบ sys/product/{RC_SN}/status_reply  { result: 0 }   ← ขั้นนี้ขาดไม่ได้!
    ↓
โดรนส่ง thing/product/{DRONE_SN}/osd  ทุก ~0.5Hz
    ↓
telemetry_listener แกะ latitude, longitude, height → SQLite (TelemetryLog)
    ↓
Dashboard เรียก GET /api/drone-location/  อัปเดตแผนที่
```

### 5.2 MQTT Topics ที่ subscribe

```python
MQTT_TOPICS = [
    "thing/product/+/osd",      # พิกัด lat/lng/height
    "thing/product/+/state",      # state อื่นๆ
    "sys/product/+/status",       # update_topo จาก RC
]
```

> **ห้าม subscribe `#`** — EMQX ACL บล็อก topic `#` โดยตรง

### 5.3 Code สำคัญ: ตอบ status_reply (`telemetry_listener.py`)

เมื่อ RC ส่ง `update_topo` ต้องตอบกลับทันที ไม่งั้นโดรนไม่ส่ง OSD:

```python
def _reply_update_topo(client, gateway_sn, payload):
    reply_topic = f"sys/product/{gateway_sn}/status_reply"
    reply = {
        "tid": payload.get("tid"),
        "bid": payload.get("bid"),
        "timestamp": int(time.time() * 1000),
        "method": payload.get("method", "update_topo"),
        "data": {"result": 0},
    }
    client.publish(reply_topic, json.dumps(reply), qos=1)
```

### 5.4 รูปแบบ OSD ที่ DJI ส่งมา

Topic: `thing/product/{DRONE_SN}/osd`

```json
{
  "tid": "...",
  "bid": "...",
  "timestamp": 1667220873846,
  "data": {
    "latitude": 13.7563,
    "longitude": 100.5018,
    "height": 50.5
  },
  "gateway": "RC_SN"
}
```

Code แกะพิกัด:

```python
data = payload.get("data", {})
lat = data.get("latitude")
lng = data.get("longitude")
alt = data.get("height") or data.get("elevation")
```

---

## 6. Video (RTMP Live Stream)

### 6.1 Flow

```
Pilot Liveshare → RTMP push → SRS :1935/live/drone
                                    ↓
Dashboard ดู FLV ← http://SERVER_IP:8085/live/drone.flv
```

### 6.2 URL ที่ใช้

| ทิศทาง | URL |
|--------|-----|
| Pilot ส่ง (RTMP push) | `rtmp://192.168.1.120:1935/live/drone` |
| Dashboard ดู (HTTP-FLV) | `http://192.168.1.120:8085/live/drone.flv` |

### 6.3 liveshareSetConfig

DJI ใช้ **2 arguments**: `(type, params_json_string)`

- `type = 2` → RTMP
- `params = {"url": "rtmp://..."}`

```javascript
window.djiBridge.liveshareSetConfig(2, JSON.stringify({ url: rtmpTargetUrl }));
```

---

## 7. Backend API ที่ Pilot อาจเรียก (Django stubs)

| Method | Path | หน้าที่ |
|--------|------|---------|
| POST | `/manage/api/v1/login/` | Login Pilot (`pilot/pilot123`, flag=2) |
| GET | `/manage/api/v1/users/current/` | คืน mqtt_addr, credentials |
| GET | `/manage/api/v1/workspaces/current/` | ข้อมูล workspace |
| POST | `/manage/api/v1/devices/{sn}/binding/` | ผูก RC/โดรนกับ workspace |
| GET | `/dji/api/v1/livestream-config/` | คืน RTMP URL |

Response มาตรฐาน DJI:

```json
{ "code": 0, "message": "success", "data": { ... } }
```

---

## 8. Credentials สรุป

| ใช้กับ | Username | Password | ไฟล์ |
|--------|----------|----------|------|
| Pilot MQTT (Thing) | `admin` | `admin1234` | H5 ปัจจุบัน |
| Pilot Login API | `pilot` | `pilot123` | `.env` |
| telemetry_listener | `admin` | `admin1234` | `.env` `MQTT_LISTENER_*` |
| EMQX Dashboard | `admin` | `admin1234` | docker-compose |

---

## 9. URL ที่ใช้บ่อย

| หน้า | URL |
|------|-----|
| H5 Handshake (Pilot) | http://192.168.1.120:8007/dji/login/ |
| Mission Dashboard | http://192.168.1.120:8007/dashboard/mission/ |
| MQTT ทดสอบ (browser RC) | http://192.168.1.120:8007/dji/mqtt-test/ |
| EMQX Dashboard | http://192.168.1.120:18083 |
| วิดีโอ FLV ตรง | http://192.168.1.120:8085/live/drone.flv |

---

## 10. Checklist ก่อนใช้งานจริง

- [ ] `docker compose up -d` — EMQX, SRS, nginx ทำงาน
- [ ] Django รันที่ `:8008` — ไม่มี 502
- [ ] `telemetry_listener.py` รันอยู่ — เห็น Subscribe OK
- [ ] RC ping ถึง `192.168.1.120` ได้
- [ ] เปิด H5 บน Pilot → กด One-Click
- [ ] Terminal listener เห็น `📤 ตอบ status_reply`
- [ ] Terminal listener เห็น `📥 [osd] SN=... lat=... lng=...`
- [ ] Dashboard: Video = Live, พิกัด = เชื่อมต่อ

---

## 11. Troubleshooting

| อาการ | สาเหตุ | แก้ |
|------|--------|-----|
| **502 Bad Gateway** | Django ไม่รัน | `runserver 0.0.0.0:8008` |
| **Video Live แต่ไม่มีพิกัด** | ไม่มี status_reply หรือ listener ไม่รัน | รัน listener ใหม่ + handshake RC อีกครั้ง |
| **Listener ไม่ได้ message** | subscribe `#` ถูก EMQX บล็อก | ใช้ topic DJI โดยตรง (แก้แล้วใน code) |
| **MQTT callback false** | ใช้ `ws://` ใน Thing module | ใช้ `tcp://IP:1883` |
| **connectCallback ไม่ทำงาน** | ชื่อ callback ผิด | ใช้ `thingConnectCallback` + ประกาศ `window.thingConnectCallback` |
| **เห็นแค่ status ไม่มี osd** | RC ส่ง update_topo ก่อน listener รัน | restart handshake หลัง listener รันแล้ว |

---

## 12. คำสั่งรวดเร็ว (copy-paste)

```bash
# เปิดทุกอย่าง
cd /home/ddm/02_drone_ai
docker compose up -d
cd drone_backend && nohup ../.venv/bin/python manage.py runserver 0.0.0.0:8008 >> ../logs/django.log 2>&1 &
cd ../ai_pipeline && python -u telemetry_listener.py
```

```bash
# ดู EMQX clients
docker exec dji_mqtt_emqx emqx ctl clients list

# ดู log
tail -f /home/ddm/02_drone_ai/logs/telemetry_listener.log
tail -f /home/ddm/02_drone_ai/logs/django.log
```
