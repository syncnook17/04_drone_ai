import os
from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse

def dji_h5_login(request):
    """ หน้าเว็บนี้จะเปิดบนรีโมต DJI RC Plus เพื่อทำ Handshake ยืนยันสิทธิ์ """
    context = {
        'app_id': settings.DJI_APP_ID,
        'app_key': settings.DJI_APP_KEY,
        'app_license': settings.DJI_APP_LICENSE,
    }
    return render(request, 'core/h5_login.html', context)

def get_mqtt_config(request):
    """ API ที่รีโมตโดรนจะเรียกเพื่อรับคอนฟิก MQTT ไปทำงาน """
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