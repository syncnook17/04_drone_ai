from django.db import models

class TelemetryLog(models.Model):
    """ ตารางเก็บพิกัดของโดรนสดทุกๆ วินาที """
    drone_sn = models.CharField(max_length=50, default="M4E", verbose_name="Serial Number โดรน")
    latitude = models.FloatField(verbose_name="ละติจูด")
    longitude = models.FloatField(verbose_name="ลองจิจูด")
    altitude = models.FloatField(verbose_name="ความสูง (เมตร)")
    heading = models.FloatField(null=True, blank=True, verbose_name="ทิศหัวโดรน (องศา, 0=เหนือ)")
    battery_percent = models.IntegerField(null=True, blank=True, verbose_name="แบตเตอรี่ (%)")
    gps_satellites = models.IntegerField(null=True, blank=True, verbose_name="จำนวนดาวเทียม GPS")
    horizontal_speed = models.FloatField(null=True, blank=True, verbose_name="ความเร็วแนวนอน (m/s)")
    vertical_speed = models.FloatField(null=True, blank=True, verbose_name="ความเร็วแนวตั้ง (m/s)")
    home_distance = models.FloatField(null=True, blank=True, verbose_name="ระยะห่างจากจุด Home (m)")
    flight_mode_code = models.CharField(
        max_length=20, null=True, blank=True,
        verbose_name="รหัสโหมดการบิน (ดิบจาก DJI mode_code)",
    )
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="เวลาที่บันทึก")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.drone_sn} - Lat: {self.latitude}, Lng: {self.longitude}"


class DetectionLog(models.Model):
    """ ตารางเก็บประวัติสิ่งที่ AI (YOLO) ตรวจจับได้จากภาพวิดีโอสด """
    OBJECT_CHOICES = [
        ('person', 'คน (Person)'),
        ('car', 'รถยนต์ (Car)'),
    ]
    object_type = models.CharField(max_length=20, choices=OBJECT_CHOICES, verbose_name="ประเภทวัตถุ")
    confidence = models.FloatField(verbose_name="ความแม่นยำ (%)")
    latitude = models.FloatField(verbose_name="ละติจูดวัตถุ")
    longitude = models.FloatField(verbose_name="ลองจิจูดวัตถุ")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="เวลาที่ตรวจเจอ")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"เจอ {self.get_object_type_display()} แม่นยำ {self.confidence}%"


class FlightControlEvent(models.Model):
    """ ตาราง audit log สำหรับการควบคุมการบินระยะไกล (DRC mode) — ใช้ตรวจสอบย้อนหลัง
    เช่น ใครขอ/ได้รับ/ปล่อยสิทธิ์ควบคุมเมื่อไหร่ และเกิด emergency stop / watchdog timeout ตอนไหน """
    EVENT_CHOICES = [
        ('requested', 'ขอสิทธิ์ควบคุม'),
        ('granted', 'ได้รับสิทธิ์ควบคุม'),
        ('denied', 'ถูกปฏิเสธ/หมดเวลา'),
        ('released', 'ปล่อยสิทธิ์ควบคุม (สั่งเอง)'),
        ('watchdog_release', 'ปล่อยสิทธิ์ควบคุมอัตโนมัติ (watchdog/disconnect)'),
        ('emergency_stop', 'สั่งหยุดฉุกเฉิน'),
        ('error', 'ข้อผิดพลาด'),
    ]
    drone_sn = models.CharField(max_length=50, default="M4E", verbose_name="Serial Number โดรน")
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES, verbose_name="ประเภทเหตุการณ์")
    detail = models.CharField(max_length=255, null=True, blank=True, verbose_name="รายละเอียดเพิ่มเติม")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="เวลาที่เกิดเหตุการณ์")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp} - {self.drone_sn} - {self.get_event_type_display()}"