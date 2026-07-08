import os
import signal

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_DIR = os.path.join(ROOT_DIR, "run")
LOG_DIR = os.path.join(ROOT_DIR, "logs")


def _pid_path(service_name: str) -> str:
    return os.path.join(PID_DIR, f"{service_name}.pid")


def _log_path(service_name: str) -> str:
    return os.path.join(LOG_DIR, f"{service_name}.log")


def is_running(service_name: str) -> bool:
    pid_file = _pid_path(service_name)
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file, "r", encoding="utf-8") as handle:
            pid = int(handle.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        if os.path.exists(pid_file):
            os.remove(pid_file)
        return False


def start_service(service_name: str, python_path: str, script_path: str) -> tuple[bool, str]:
    os.makedirs(PID_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    if is_running(service_name):
        return False, f"{service_name} กำลังทำงานอยู่แล้ว"

    log_file = open(_log_path(service_name), "a", encoding="utf-8")
    import subprocess

    process = subprocess.Popen(
        [python_path, script_path],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    with open(_pid_path(service_name), "w", encoding="utf-8") as handle:
        handle.write(str(process.pid))
    return True, f"{service_name} เริ่มทำงานแล้ว (PID {process.pid})"


def stop_service(service_name: str) -> tuple[bool, str]:
    pid_file = _pid_path(service_name)
    if not os.path.exists(pid_file):
        return False, f"{service_name} ไม่ได้ทำงานอยู่"

    try:
        with open(pid_file, "r", encoding="utf-8") as handle:
            pid = int(handle.read().strip())
        os.kill(pid, signal.SIGTERM)
        os.remove(pid_file)
        return True, f"{service_name} หยุดทำงานแล้ว"
    except (OSError, ValueError) as exc:
        if os.path.exists(pid_file):
            os.remove(pid_file)
        return False, str(exc)
