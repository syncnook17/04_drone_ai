# 🛸 DJI Matrice 4E Autonomous Flight & CV Dashboard (Prototype)

โปรเจกต์ระบบต้นแบบสำหรับสั่งการบินโดรน DJI Matrice 4E อัตโนมัติผ่าน Cloud API, ประมวลผลภาพสดด้วย Computer Vision (YOLO) บน Ubuntu Server, บันทึกข้อมูลพิกัดลง Database และแสดงผลแผนที่ความหนาแน่น (Heatmap) ด้วย Django

---

## 🛠️ ระบบที่ต้องเตรียม (Prerequisites & Infrastructure)

### 1. DJI Developer Credentials
ต้องลงทะเบียนที่ [DJI Developer Portal](https://developer.dji.com/) และสร้างแอปพลิเคชันประเภท **Cloud API** เพื่อนำค่าต่อไปนี้มาใส่ในระบบยืนยันตัวตน (H5 Page):
* `APP_ID`
* `APP_KEY`
* `APP_LICENSE`

### 2. ฮาร์ดแวร์และอุปกรณ์หน้างาน
* **โดรน:** DJI Matrice 4E (พร้อมเปิดสิทธิ์การใช้งานผ่านอินเทอร์เน็ต/4G Dongle)
* **รีโมตคอนโทรล:** DJI RC Plus 2 (พร้อมแอป DJI Pilot 2)

### 3. ซอฟต์แวร์บน Ubuntu Server
* **OS:** Ubuntu Server (แนะนำ 22.04 LTS หรือใหม่กว่า)
* **Web Framework:** Django (พร้อมติดตั้ง `Django Channels` หากต้องการส่งพิกัดขึ้นหน้าเว็บสด)
* **Media Server:** SRS (Simple Realtime Server) รันผ่าน Docker สำหรับรับสตรีม RTMP
* **MQTT Broker:** EMQX หรือ Mosquitto สำหรับรับ Telemetry JSON จากโดรน
* **Database:** PostgreSQL (แนะนำ) หรือ MySQL
* **AI Engine:** Python + OpenCV + Ultralytics YOLOv8/v10

---

## 📂 โครงสร้างระบบสถาปัตยกรรม (Architecture Overview)

1. **Mission Planning:** หน้าเว็บ (Django) สร้างไฟล์เส้นทางบิน `.KMZ` -> ส่งผ่าน Cloud API เข้าโดรน -> โดรนเริ่มบินอัตโนมัติ
2. **Telemetry Streaming:** โดรนส่งพิกัด GPS ปัจจุบันผ่าน MQTT -> Python Listener แกะค่า -> บันทึกลง Database -> Django อัปเดตพิกัดโดรนบนแผนที่สด (WebSocket)
3. **Video Processing:** รีโมตส่งสตรีมภาพสดผ่าน RTMP -> เข้า Media Server -> สคริปต์ Python AI ดึงเฟรมภาพมาตรวจจับ "คน" และ "รถ" -> บันทึกเวลาและพิกัดลงฐานข้อมูล
4. **Data Visualization:** Django ดึงพิกัดจากฐานข้อมูลมาพล็อตเป็น Heatmap ความหนาแน่นบนแผนที่หน้าเว็บ และแสดงผลในตารางข้อมูล LOG

---

## 🌐 โครงสร้างหน้าเว็บระบบ (Web Interface Requirements)

### หน้าที่ 1: สั่งการบินและควบคุม (Mission Control)
* **ฝั่งซ้าย (แผนที่):** ใช้ Leaflet.js สำหรับคลิกวางจุดพิกัดการบิน, แสดงพิกัดตัวโดรนปัจจุบันแบบ Real-time, และกดปุ่มสั่ง "บินอัตโนมัติ"
* **ฝั่งขวา (วิดีโอสด):** ตัวเล่นวิดีโอสตรีมสด (WebRTC/Whip) ที่ดึงมาจาก Media Server 
* **ฟีเจอร์เพิ่มพิเศษ (Smart Track):** ผู้ใช้สามารถลากเส้นกล่องสี่เหลี่ยมบนวิดีโอ เพื่อสั่งล็อกเป้าหมายให้โดรนและกิมบอลกล้องหันบินติดตามวัตถุนั้นโดยอัตโนมัติผ่านคำสั่ง `Follow by Box Selection` ของ DJI Cloud API

### หน้าที่ 2: แผนที่ความร้อน (Live Heatmap)
* แผนที่แสดงความหนาแน่นเชิงพื้นที่ (Density Map) โดยดึงข้อมูลพิกัด (Lat, Lng) ที่ตรวจเจอคนหรือรถจากฐานข้อมูลมาพล็อตด้วย `Leaflet.heat` หรือ `heatmap.js`

### หน้าที่ 3: ตารางบันทึกข้อมูล (Log Table Dashboard)
* ตารางแสดงประวัติข้อมูลการตรวจจับแบบ Real-time (ดึงข้อมูลด้วย Django ORM)
* คอลัมน์ที่แสดง: `ลำดับ`, `เวลา (Timestamp)`, `ประเภทวัตถุ (คน/รถ)`, `ค่าความมั่นใจ (Confidence)`, `พิกัดละติจูด`, `พิกัดลองจิจูด`

---

## 🚀 ขั้นตอนการเริ่มพัฒนาต้นแบบ (Getting Started Workflow)

1. **Setup Core Services:** รัน MQTT Broker และ SRS Media Server บน Ubuntu (แนะนำให้ใช้ Docker เพื่อความรวดเร็ว)
2. **Django Handshake:** พัฒนาหน้าเว็บ H5 Login บน Django โดยใส่ App ID, App Key และ App License จาก DJI เพื่อให้รีโมต Pilot 2 สามารถกดเชื่อมต่อเข้า Ubuntu Server ของเราได้
3. **Build Telemetry Listener:** เขียนสคริปต์ Python รอรับค่าพิกัดโดรนจาก MQTT และบันทึกลง Database
4. **Build AI Video Pipeline:** เขียนสคริปต์ Python เปิดอ่านสตรีม RTMP จาก SRS -> รันโมเดล YOLO -> เมื่อเจอวัตถุให้นำพิกัดโดรน ณ วินาทีนั้นมาบันทึกร่วมกัน
5. **Frontend Integration:** สร้างหน้า Dashboard แผนที่และตารางบน Django เพื่อแสดงผลข้อมูลทั้งหมดแบบ Real-time


Roadmap 5 เฟส ที่เราจะเดินไปด้วยกันตั้งแต่วันนี้ครับ:

เฟส 1: เตรียมโครงสร้างโฟลเดอร์ และรันระบบหลังบ้าน (MQTT & Media Server) 🚀 (เราจะเริ่มข้อนี้กันเลย)

เฟส 2: สร้างโปรเจกต์ Django และทำระบบ Handshake (H5 Login) ของ DJI

เฟส 3: ดึงพิกัดโดรน (Telemetry) บันทึกลง Database

เฟส 4: ดึงวิดีโอสด (RTMP) มาให้ AI (YOLO) ตรวจจับคน/รถ

เฟส 5: พัฒนาหน้าเว็บ 3 หน้า (แผนที่, Heatmap, ตาราง) และทดสอบระบบจริง