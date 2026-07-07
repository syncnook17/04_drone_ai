import os
import sys
import cv2
from dotenv import load_dotenv
from ultralytics import YOLO

# 1. โหลดค่าคอนฟิกจากไฟล์ .env ด้านนอกสุด
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# 2. เชื่อมต่อระบบเข้ากับฐานข้อมูล Django ORM
sys.path.append(os.path.join(ROOT_DIR, 'drone_backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'drone_system.settings')
import django
django.setup()

# ดึง Models มาใช้งาน
from app_core.models import TelemetryLog, DetectionLog

# 3. ตั้งค่าที่มาของวิดีโอ (RTMP Stream)
# สำหรับ Matrice 4E เมื่อสตรีมเข้า SRS ผ่าน Docker พอร์ต 1935 URL จะเป็นแบบนี้
# (คุณสามารถเปลี่ยนชื่อ 'live/drone' ให้ตรงกับ Stream Key ที่ตั้งบนรีโมตได้)
RTMP_URL = "rtmp://localhost:1935/live/drone"

def main():
    print("🧠 กำลังโหลดโมเดล AI (YOLOv8)...")
    # ระบบจะดาวน์โหลดไฟล์โมเดลขนาดเล็ก (yolov8n.pt) มาให้อัตโนมัติในครั้งแรก
    model = YOLO("yolov8n.pt") 
    
    print(f"📹 กำลังเปิดการเชื่อมต่อสตรีมวิดีโอ: {RTMP_URL}")
    cap = cv2.VideoCapture(RTMP_URL)
    
    # กำหนดคลาสที่ต้องการตรวจจับตามมาตรฐาน COCO Dataset (0 = คน, 2 = รถยนต์)
    TARGET_CLASSES = [0, 2] 
    
    if not cap.isOpened():
        print("❌ ไม่สามารถเปิดสตรีม RTMP ได้ (รอให้รีโมตโดรนเริ่มส่งสัญญาณภาพเข้ามาก่อน)")
        # ในการทดสอบจริง โค้ดจะวนลูปพยายามต่อใหม่จนกว่าสัญญาณภาพจะมา
    
    print("🚀 ระบบ AI Video Processor พร้อมทำงานแล้ว! กำลังรอเฟรมภาพ...")
    
    frame_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            # หากเฟรมหลุด ให้รอสักครู่แล้วข้ามไปเฟรมถัดไป
            continue
            
        frame_count += 1
        # เพื่อไม่ให้เซิร์ฟเวอร์ทำงานหนักเกินไป เราจะให้ AI ประมวลผลทุกๆ 5 เฟรม (ประมาณ 3-6 เฟรมต่อวินาที)
        if frame_count % 5 != 0:
            continue

        # ส่งเฟรมภาพให้ YOLO ประมวลผล
        results = model.predict(frame, verbose=False, classes=TARGET_CLASSES)[0]
        
        # วิ่งไปดึงพิกัดล่าสุดของโดรนจากตาราง TelemetryLog มาเตรียมไว้
        latest_telemetry = TelemetryLog.objects.first()
        
        if latest_telemetry:
            current_lat = latest_telemetry.latitude
            current_lng = latest_telemetry.longitude
        else:
            # หากยังไม่มีโดรนส่งพิกัดมาเลย ให้ใช้พิกัดจำลองไปก่อนชั่วคราวในการทดสอบ
            current_lat = 13.7563 
            current_lng = 100.5018

        # ลูปตรวจสอบสิ่งที่ AI ตรวจเจอในเฟรมนี้
        for box in results.boxes:
            class_id = int(box.cls[0])
            confidence = float(box.conf[0]) * 100 # แปลงเป็นเปอร์เซ็นต์
            
            # กรองเอาเฉพาะวัตถุที่ความแม่นยำมากกว่า 50% ขึ้นไป
            if confidence > 50:
                obj_type = 'person' if class_id == 0 else 'car'
                
                # 4. บันทึกผลลัพธ์ลง Database ของ Django
                DetectionLog.objects.create(
                    object_type=obj_type,
                    confidence=round(confidence, 2),
                    latitude=current_lat,
                    longitude=current_lng
                )
                print(f"🎯 [AI พบวัตถุ] ประเภท: {obj_type} ({round(confidence,1)}%) ณ พิกัดโดรน: {current_lat}, {current_lng}")

        # ส่วนทดสอบ: ถ้าคุณรันสคริปต์นี้บน Ubuntu แบบมีหน้าจอ (GUI) 
        # สามารถเปิดคอมเมนต์ 2 บรรทัดด้านล่างนี้เพื่อดูภาพติดกล่อง AI สดๆ ได้ครับ
        # cv2.imshow("Drone AI Feed", results.plot())
        # if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 ปิดระบบ AI Video Processor เรียบร้อย")