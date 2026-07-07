from django.db import models

class TelemetryLog(models.Model):
    """ ตารางเก็บพิกัดของโดรนสดทุกๆ วินาที """
    drone_sn = models.CharField(max_length=50, default="M4E", verbose_name="Serial Number โดรน")
    latitude = models.FloatField(verbose_name="ละติจูด")
    longitude = models.FloatField(verbose_name="ลองจิจูด")
    altitude = models.FloatField(verbose_name="ความสูง (เมตร)")
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