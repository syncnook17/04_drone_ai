import json
import os
import socket
import sys
import uuid
from datetime import timedelta
from urllib.error import URLError
from urllib.request import urlopen

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from app_core.models import DetectionLog, TelemetryLog

ROOT_DIR = settings.BASE_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai_pipeline.service_manager import is_running, start_service, stop_service  # noqa: E402


def _rtmp_url() -> str:
    return (
        f"rtmp://{settings.SERVER_IP}:{settings.RTMP_PORT}/"
        f"{settings.RTMP_APP}/{settings.RTMP_STREAM_KEY}"
    )


def _flv_url() -> str:
    return (
        f"http://{settings.SERVER_IP}:{settings.SRS_HTTP_PORT}/"
        f"{settings.RTMP_APP}/{settings.RTMP_STREAM_KEY}.flv"
    )


def _venv_python() -> str:
    venv_python = os.path.join(ROOT_DIR, ".venv", "bin", "python")
    return venv_python if os.path.exists(venv_python) else sys.executable


def dji_h5_login(request):
    context = {
        "app_id": settings.DJI_APP_ID,
        "app_key": settings.DJI_APP_KEY,
        "app_license": settings.DJI_APP_LICENSE,
        "workspace_id": settings.DJI_WORKSPACE_ID,
        "platform_name": settings.DJI_PLATFORM_NAME,
        "workspace_name": settings.DJI_WORKSPACE_NAME,
        "workspace_desc": settings.DJI_WORKSPACE_DESC,
        "server_ip": settings.SERVER_IP,
        "django_port": settings.DJANGO_PORT,
        "api_host": f"http://{settings.SERVER_IP}:{settings.DJANGO_PORT}",
    }
    return render(request, "app_core/h5_login.html", context)


def dji_mqtt_test(request):
    username, password = _mqtt_credentials()
    return render(
        request,
        "app_core/mqtt_test.html",
        {
            "server_ip": settings.SERVER_IP,
            "django_port": settings.DJANGO_PORT,
            "ws_url": _ws_mqtt_addr(),
            "mqtt_user": username,
            "mqtt_password": password,
        },
    )


def _mqtt_credentials():
    if settings.MQTT_USE_ANONYMOUS:
        return "", ""
    return settings.MQTT_USER, settings.MQTT_PASSWORD


def _ws_mqtt_addr() -> str:
    if settings.MQTT_WS_USE_GATEWAY:
        return f"ws://{settings.SERVER_IP}:{settings.DJANGO_PORT}{settings.MQTT_WS_GATEWAY_PATH}"
    return f"ws://{settings.SERVER_IP}:{settings.MQTT_WS_PORT}/mqtt"


def get_mqtt_config(request):
    mqtt_addr = f"tcp://{settings.SERVER_IP}:{settings.MQTT_PUBLIC_PORT}"
    ws_addr = _ws_mqtt_addr()
    username, password = _mqtt_credentials()
    return JsonResponse(
        {
            "code": 0,
            "message": "success",
            "data": {
                "host": settings.SERVER_IP,
                "port": settings.MQTT_PUBLIC_PORT,
                "mqtt_addr": mqtt_addr,
                "ws_mqtt_addr": ws_addr,
                "username": username,
                "password": password,
                "use_anonymous": settings.MQTT_USE_ANONYMOUS,
            },
        }
    )


def _validate_pilot_login(body):
    username = body.get("username", "")
    password = body.get("password", "")
    flag = int(body.get("flag", 2))
    if flag != 2:
        return False, "account type mismatch (flag must be 2)"
    if username != settings.PILOT_USERNAME or password != settings.PILOT_PASSWORD:
        return False, "invalid username or password"
    return True, ""


def _pilot_login_payload(body=None):
    body = body or {}
    mqtt_user, mqtt_pass = _mqtt_credentials()
    token = uuid.uuid4().hex
    mqtt_addr = f"tcp://{settings.SERVER_IP}:{settings.MQTT_PUBLIC_PORT}"
    ws_addr = _ws_mqtt_addr()
    login_user = body.get("username", settings.PILOT_USERNAME)

    return {
        "username": login_user,
        "user_id": login_user,
        "access_token": token,
        "workspace_id": settings.DJI_WORKSPACE_ID,
        "mqtt_addr": mqtt_addr,
        "ws_mqtt_addr": ws_addr,
        "mqtt_username": mqtt_user,
        "mqtt_password": mqtt_pass,
        "platform_name": settings.DJI_PLATFORM_NAME,
        "workspace_name": settings.DJI_WORKSPACE_NAME,
        "workspace_desc": settings.DJI_WORKSPACE_DESC,
    }


def _dji_ok(data=None):
    return JsonResponse({"code": 0, "message": "success", "data": data or {}})


def _dji_err(message, code=401):
    return JsonResponse({"code": code, "message": message, "data": None}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def dji_pilot_login(request):
    """DJI Pilot login — คืน token + MQTT config ตามลำดับ Cloud API มาตรฐาน"""
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        body = {}

    ok, message = _validate_pilot_login(body)
    if not ok:
        return _dji_err(message)

    return _dji_ok(_pilot_login_payload(body))


@csrf_exempt
@require_http_methods(["POST"])
def manage_login(request):
    """Alias มาตรฐาน DJI: POST /manage/api/v1/login"""
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        body = {}

    ok, message = _validate_pilot_login(body)
    if not ok:
        return _dji_err(message)

    return _dji_ok(_pilot_login_payload(body))


def manage_workspace_current(request):
    return _dji_ok(
        {
            "id": settings.DJI_WORKSPACE_ID,
            "workspace_id": settings.DJI_WORKSPACE_ID,
            "name": settings.DJI_WORKSPACE_NAME,
            "workspace_name": settings.DJI_WORKSPACE_NAME,
            "platform_name": settings.DJI_PLATFORM_NAME,
            "desc": settings.DJI_WORKSPACE_DESC,
        }
    )


def manage_devices_list(request, workspace_id):
    return _dji_ok({"list": [], "pagination": {"page": 1, "page_size": 50, "total": 0}})


def manage_devices_bound(request, workspace_id):
    return _dji_ok({"list": [], "pagination": {"page": 1, "page_size": 50, "total": 0}})


def manage_device_detail(request, workspace_id, device_sn):
    return _dji_ok(
        {
            "device_sn": device_sn,
            "device_name": device_sn,
            "workspace_id": workspace_id,
            "bound_status": True,
        }
    )


def get_livestream_config(request):
    return JsonResponse(
        {
            "code": 0,
            "message": "success",
            "data": {
                "type": 1,
                "url": _rtmp_url(),
                "url_type": 1,
                "rtmp_url": _rtmp_url(),
                "flv_url": _flv_url(),
            },
        }
    )


def mission_control(request):
    return render(
        request,
        "app_core/mission_control.html",
        {
            "server_ip": settings.SERVER_IP,
            "django_port": settings.DJANGO_PORT,
            "flv_url": _flv_url(),
            "rtmp_url": _rtmp_url(),
        },
    )


def heatmap_view(request):
    return render(request, "app_core/heatmap.html")


def detection_logs(request):
    logs = DetectionLog.objects.all()[:100]
    return render(request, "app_core/detection_logs.html", {"logs": logs})


def api_latest_drone_location(request):
    latest = TelemetryLog.objects.first()
    if not latest:
        return JsonResponse(
            {
                "connected": False,
                "lat": None,
                "lng": None,
                "alt": None,
                "message": "ยังไม่ได้รับพิกัดจากโดรน",
            }
        )

    age = timezone.now() - latest.timestamp
    connected = age <= timedelta(seconds=settings.TELEMETRY_STALE_SECONDS)
    return JsonResponse(
        {
            "connected": connected,
            "lat": latest.latitude,
            "lng": latest.longitude,
            "alt": latest.altitude,
            "drone_sn": latest.drone_sn,
            "updated_at": latest.timestamp.isoformat(),
            "message": "เชื่อมต่อแล้ว" if connected else "พิกัดล้าสมัย — ตรวจสอบ DJI Pilot 2",
        }
    )


def api_heatmap_data(request):
    detections = DetectionLog.objects.all().values("latitude", "longitude")
    points = [[d["latitude"], d["longitude"]] for d in detections]
    return JsonResponse({"points": points})


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _srs_stream_active() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{settings.SRS_API_PORT}/api/v1/streams/", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return False

    target = f"/{settings.RTMP_APP}/{settings.RTMP_STREAM_KEY}"
    for stream in payload.get("streams", []):
        if stream.get("url") == target and stream.get("publish", {}).get("active"):
            return True
    return False


def api_system_status(request):
    latest = TelemetryLog.objects.first()
    telemetry_connected = False
    if latest:
        age = timezone.now() - latest.timestamp
        telemetry_connected = age <= timedelta(seconds=settings.TELEMETRY_STALE_SECONDS)

    mqtt_host = settings.SERVER_IP
    mqtt_port = settings.MQTT_PUBLIC_PORT
    mqtt_broker_ok = _tcp_reachable(mqtt_host, mqtt_port)
    srs_ok = _tcp_reachable(settings.SERVER_IP, settings.RTMP_PORT)

    return JsonResponse(
        {
            "telemetry_listener_running": is_running("telemetry_listener"),
            "video_processor_running": is_running("video_processor"),
            "telemetry_connected": telemetry_connected,
            "telemetry_count": TelemetryLog.objects.count(),
            "detection_count": DetectionLog.objects.count(),
            "video_stream_active": _srs_stream_active(),
            "mqtt_broker_ok": mqtt_broker_ok,
            "srs_ok": srs_ok,
            "flv_url": _flv_url(),
            "rtmp_url": _rtmp_url(),
            "mqtt_host": f"{mqtt_host}:{mqtt_port}",
            "pilot_h5_url": f"http://{settings.SERVER_IP}:{settings.DJANGO_PORT}/dji/login/",
        }
    )


def start_backend_service(request):
    service_type = request.GET.get("service")
    action = request.GET.get("action", "start")
    python_path = _venv_python()

    if service_type == "telemetry":
        service_name = "telemetry_listener"
        script_path = os.path.join(ROOT_DIR, "ai_pipeline", "telemetry_listener.py")
    elif service_type == "ai":
        service_name = "video_processor"
        script_path = os.path.join(ROOT_DIR, "ai_pipeline", "video_processor.py")
    else:
        return JsonResponse({"status": "error", "message": "ไม่พบประเภทบริการที่ระบุ"})

    try:
        if action == "stop":
            ok, message = stop_service(service_name)
        else:
            ok, message = start_service(service_name, python_path, script_path)

        return JsonResponse(
            {
                "status": "success" if ok else "warning",
                "message": message,
                "running": is_running(service_name),
            }
        )
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)})
