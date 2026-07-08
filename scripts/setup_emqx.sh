#!/usr/bin/env bash
# =============================================================
# setup_emqx.sh — ตั้งค่า EMQX Authentication หลัง docker up
# รัน: bash scripts/setup_emqx.sh
# =============================================================
set -e

EMQX_HOST="${EMQX_HOST:-http://localhost:18083}"
EMQX_ADMIN_PASS="${EMQX_DASHBOARD__DEFAULT_PASSWORD:-admin1234}"

echo "🔧 กำลังตั้งค่า EMQX Authentication..."

# --- รอ EMQX พร้อม ---
echo "⏳ รอ EMQX เริ่มต้น..."
for i in $(seq 1 30); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$EMQX_HOST/api/v5/status" 2>/dev/null || true)
    if [ "$STATUS" = "200" ]; then
        echo "✅ EMQX พร้อม"
        break
    fi
    sleep 2
    echo "   รอ... ($i/30)"
done

# --- Login ขอ token ---
echo "🔑 Login EMQX Dashboard..."
TOKEN=$(curl -s -X POST "$EMQX_HOST/api/v5/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$EMQX_ADMIN_PASS\"}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token','ERROR'))" 2>/dev/null)

if [ "$TOKEN" = "ERROR" ] || [ -z "$TOKEN" ]; then
    echo "❌ Login EMQX ล้มเหลว — ตรวจสอบ EMQX_DASHBOARD__DEFAULT_PASSWORD"
    exit 1
fi
echo "✅ Token ได้แล้ว"

AUTH_HEADER="Authorization: Bearer $TOKEN"

# --- สร้าง built-in database authenticator (ถ้าไม่มี) ---
echo "📋 ตั้งค่า Built-in Auth Database..."
RESULT=$(curl -s -X POST "$EMQX_HOST/api/v5/authentication" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d '{
        "mechanism": "password_based",
        "backend": "built_in_database",
        "password_hash_algorithm": {
            "name": "plain",
            "salt_position": "disable"
        },
        "user_id_type": "username"
    }')

CODE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('code','ok'))" 2>/dev/null || echo "ok")
if [ "$CODE" = "ALREADY_EXISTS" ]; then
    echo "   ℹ️  Built-in Auth มีอยู่แล้ว"
else
    echo "   ✅ สร้าง Built-in Auth สำเร็จ"
fi

# --- เพิ่ม/อัปเดต users ---
add_user() {
    local USERNAME="$1"
    local PASSWORD="$2"
    local IS_SUPER="$3"
    
    RESULT=$(curl -s -X POST \
        "$EMQX_HOST/api/v5/authentication/password_based%3Abuilt_in_database/users" \
        -H "Content-Type: application/json" \
        -H "$AUTH_HEADER" \
        -d "{\"user_id\":\"$USERNAME\",\"password\":\"$PASSWORD\",\"is_superuser\":$IS_SUPER}")
    
    CODE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('code','ok'))" 2>/dev/null || echo "ok")
    if [ "$CODE" = "ALREADY_EXISTS" ]; then
        curl -s -X PUT \
            "$EMQX_HOST/api/v5/authentication/password_based%3Abuilt_in_database/users/$USERNAME" \
            -H "Content-Type: application/json" \
            -H "$AUTH_HEADER" \
            -d "{\"password\":\"$PASSWORD\",\"is_superuser\":$IS_SUPER}" > /dev/null
        echo "   ♻️  อัปเดต user: $USERNAME"
    else
        echo "   ✅ เพิ่ม user: $USERNAME"
    fi
}

echo "👤 ตั้งค่า MQTT Users..."
add_user "pilot"  "pilot123"  "false"
add_user "admin"  "admin1234" "true"

# --- ตรวจสอบสุดท้าย ---
echo ""
echo "=== ✅ EMQX Setup เสร็จสมบูรณ์ ==="
USERS=$(curl -s \
    "$EMQX_HOST/api/v5/authentication/password_based%3Abuilt_in_database/users" \
    -H "$AUTH_HEADER" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); [print('  •', u['user_id']) for u in d.get('data',[])]" 2>/dev/null)
echo "MQTT Users ที่ตั้งค่าแล้ว:"
echo "$USERS"

echo ""
echo "🧪 ทดสอบ WebSocket MQTT (:8007/mqtt):"
# หมายเหตุ: curl timeout หลัง WebSocket upgrade → exit non-zero แม้ได้ 101
# ใช้ temp file แทน subshell เพื่อหลีกเลี่ยง "101000" จาก || echo "000"
WS_TMP=$(mktemp)
curl -s -o /dev/null -w "%{http_code}" --http1.1 \
    -H "Upgrade: websocket" \
    -H "Connection: Upgrade" \
    -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Protocol: mqtt" \
    --max-time 3 \
    http://localhost:8007/mqtt > "$WS_TMP" 2>/dev/null || true
WS_CODE=$(cat "$WS_TMP"); rm -f "$WS_TMP"
if [ "$WS_CODE" = "101" ]; then
    echo "✅ WebSocket Gateway → HTTP 101 Switching Protocols (พร้อมใช้งาน!)"
else
    echo "⚠️  WebSocket ตอบ HTTP $WS_CODE (ควรได้ 101)"
fi
