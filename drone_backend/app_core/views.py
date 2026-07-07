import os
from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse
from app_core.models import TelemetryLog, DetectionLog # ดึง Models มาร่วมใช้

# --- โค้ดเดิมของ DJI H5 Login และ MQTT Config (ปล่อยไว้เหมือนเดิม) ---
def dji_h5_login(request):
    context = {
        'app_id': settings.DJI_APP_ID,
        'app_key': settings.DJI_APP_KEY,
        'app_license': settings.DJI_APP_LICENSE,
    }
    return render(request, 'app_core/h5_login.html', context)

def get_mqtt_config(request):
    config = {
        "code": 0,
        "message": "success",
        "data": {
            "host": settings.SERVER_IP,
            "port": 1883,
            "username": os.getenv('MQTT_USER', 'admin'),
            "password": os.getenv('MQTT_PASSWORD', 'admin1234')
        }
    }
    return JsonResponse(config)

# ========================================================
# โค้ดใหม่ของเฟส 5: หน้าเว็บ DASHBOARD ทั้ง 3 หน้า
# ========================================================

def mission_control(request):
    """ หน้าที่ 1: สั่งการบินและดูภาพสดพร้อมตำแหน่งโดรน """
    return render(request, 'app_core/mission_control.html', {'server_ip': settings.SERVER_IP})

def heatmap_view(request):
    """ หน้าที่ 2: แผนที่แสดงความหนาแน่น Heatmap ของวัตถุ """
    return render(request, 'app_core/heatmap.html')

def detection_logs(request):
    """ หน้าที่ 3: ตารางแสดงประวัติ LOG ที่ AI ตรวจเจอ """
    logs = DetectionLog.objects.all()[:100] # ดึง 100 รายการล่าสุดมาโชว์
    return render(request, 'app_core/detection_logs.html', {'logs': logs})

# ========================================================
# API ย่อยสำหรับให้หน้าเว็บดึงข้อมูลพิกัดสด (AJAX Polling)
# ========================================================

def api_latest_drone_location(request):
    """ API ส่งพิกัดล่าสุดของโดรนให้หน้า Mission Control """
    latest = TelemetryLog.objects.first()
    if latest:
        return JsonResponse({
            "lat": latest.latitude,
            "lng": latest.longitude,
            "alt": latest.altitude
        })
    return JsonResponse({"lat": 13.7563, "lng": 100.5018, "alt": 0}) # พิกัดกรุงเทพฯ เริ่มต้นหากไม่มีข้อมูล

def api_heatmap_data(request):
    """ API ดึงพิกัดทั้งหมดที่ AI ตรวจเจอเพื่อไปทำ Heatmap """
    # ดึงพิกัดวัตถุทั้งหมดส่งเป็น Array ออกไป
    detections = DetectionLog.objects.all().values('latitude', 'longitude')
    points = [[d['latitude'], d['longitude']] for d in detections]
    return JsonResponse({"points": points})

def start_backend_service(request):
    """ API สำหรับสั่งรันสคริปต์ Telemetry หรือ AI วิ่งใน Background """
    service_type = request.GET.get('service') # รับค่า 'telemetry' หรือ 'ai'
    
    # คำนวณหาตำแหน่งที่อยู่ของไฟล์สคริปต์และ Python Virtual Environment (.venv)
    # โครงสร้าง: จาก drone_backend ขยับขึ้นไป 1 ชั้นจะเจอ .venv และ ai_pipeline
    venv_python = os.path.join(settings.BASE_DIR.parent, '.venv', 'bin', 'python')
    
    # Fallback หากรันบน Windows หรือระบบหาพาทเวอชวลเอนไวรอนเมนต์ไม่เจอ
    if not os.path.exists(venv_python):
        venv_python = sys.executable

    if service_type == 'telemetry':
        script_path = os.path.join(settings.BASE_DIR.parent, 'ai_pipeline', 'telemetry_listener.py')
        msg_success = "Telemetry Listener เริ่มทำงานแล้ว"
    elif service_type == 'ai':
        script_path = os.path.join(settings.BASE_DIR.parent, 'ai_pipeline', 'video_processor.py')
        msg_success = "AI Video Processor เริ่มทำงานแล้ว"
    else:
        return JsonResponse({"status": "error", "message": "ไม่พบประเภทบริการที่ระบุ"})

    try:
        # สั่งรันคำสั่ง Python แบบ Asynchronous (ไม่บล็อกการทำงานของหน้าเว็บ)
        subprocess.Popen(
            [venv_python, script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True # เปิดเซสชันใหม่เพื่อให้ทำงานค้างไว้แม้ปิดบราวเซอร์
        )
        return JsonResponse({"status": "success", "message": msg_success})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})