import os
import sys
import json
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# 1. โหลดค่าคอนฟิกจากไฟล์ .env ด้านนอกสุด
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# 2. จำลองสภาพแวดล้อมเพื่อให้สคริปต์นี้นำ Django ORM มาใช้งานได้
sys.path.append(os.path.join(ROOT_DIR, 'drone_backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'drone_system.settings')
import django
django.setup()

# ดึง Model ของ Django มาใช้งาน
from app_core.models import TelemetryLog

# ตั้งค่าเน็ตเวิร์กของ MQTT (เชื่อมเข้าตัว EMQX Docker บน Ubuntu ตัวเอง)
MQTT_HOST = "localhost" 
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "admin")
MQTT_PASS = os.getenv("MQTT_PASSWORD", "admin1234")

# DJI Cloud API v2 จะส่งข้อมูล OSD (พิกัด, แบต) มาที่ Topic โครงสร้างนี้
# เครื่องหมาย + คือ wildcard แทนตำแหน่ง Serial Number ของโดรน
MQTT_TOPIC = "thing/product/+/osd"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ เชื่อมต่อ MQTT Broker (EMQX) สำเร็จ!")
        client.subscribe(MQTT_TOPIC)
        print(f"📡 กำลังสแตนด์บายรอรับพิกัดจาก Topic: {MQTT_TOPIC}")
    else:
        print(f"❌ เชื่อมต่อล้มเหลวด้วยรหัสข้อผิดพลาด: {rc}")

def on_message(client, userdata, msg):
    try:
        # แปลงข้อมูล JSON ที่ส่งมาจากโดรน DJI
        payload = json.loads(msg.payload.decode('utf-8'))
        data = payload.get('data', {})
        
        # แกะโครงสร้างข้อมูลพิกัดตามมาตรฐานของ DJI Cloud API
        lat = data.get('latitude')
        lng = data.get('longitude')
        alt = data.get('height') # หรือ 'elevation' ขึ้นอยู่กับเวอร์ชันเฟิร์มแวร์
        
        # แกะข้อมูล Serial Number ของโดรนจากชื่อ Topic
        topic_parts = msg.topic.split('/')
        drone_sn = topic_parts[2] if len(topic_parts) > 2 else "M4E"

        if lat is not None and lng is not None:
            # 3. บันทึกข้อมูลพิกัดลง Database ผ่าน Django ORM ทันที
            log = TelemetryLog.objects.create(
                drone_sn=drone_sn,
                latitude=float(lat),
                longitude=float(lng),
                altitude=float(alt) if alt else 0.0
            )
            print(f"📥 [บันทึกพิกัด] โดรน SN: {drone_sn} -> ละติจูด: {lat}, ลองจิจูด: {lng}, สูง: {alt or 0} ม.")
            
    except Exception as e:
        print(f"⚠️ เกิดข้อผิดพลาดในการประมวลผลข้อมูล: {e}")

# เริ่มต้นทำงานระบบ MQTT
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("\n🛑 ปิดระบบ Telemetry Listener เรียบร้อย")