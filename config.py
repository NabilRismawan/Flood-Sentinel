"""
config.py - Semua pengaturan FloodSentinel Dashboard (Milestone 5).

Ubah nilai di sini saja. Beberapa nilai bisa ditimpa lewat environment variable
(berguna untuk uji coba tanpa mengedit file):
    FS_BROKER_HOST   host broker   (default broker.hivemq.com)
    FS_BROKER_PORT   port broker   (default 1883)
    FS_TOPIC_BASES   daftar basis topic, dipisah koma

PENTING: seluruh batas ketinggian di bawah adalah PARAMETER SIMULASI.
Bukan standar keselamatan banjir di lapangan.
"""

import os

# ----------------------------------------------------------------------------
# MQTT broker (HiveMQ Public Broker, tanpa login)
# ----------------------------------------------------------------------------
BROKER_HOST = os.environ.get("FS_BROKER_HOST", "broker.hivemq.com")
BROKER_PORT = int(os.environ.get("FS_BROKER_PORT", "1883"))   # TCP biasa
KEEPALIVE_S = 30
QOS = 0

# ----------------------------------------------------------------------------
# Topic
# Dashboard mendengarkan <basis>/telemetry, <basis>/status, <basis>/online
# untuk SETIAP basis di bawah.
#
# Kenapa dua basis? Dokumen spesifikasi Milestone 5 menyebut basis pertama,
# sedangkan sketch .ino yang sedang dipakai mengirim ke basis kedua.
# Dashboard memakai basis yang benar-benar mengirim data. Setelah topic di .ino
# final, hapus basis yang tidak dipakai.
# ----------------------------------------------------------------------------
_DEFAULT_BASES = [
    "floodsentinel/sic-b8/demo01/device01",   # sesuai dokumen Milestone 5
    "floodsentinel/13223072",                 # sesuai sketch .ino terbaru
]
TOPIC_BASES = [
    b.strip().strip("/")
    for b in os.environ.get("FS_TOPIC_BASES", ",".join(_DEFAULT_BASES)).split(",")
    if b.strip()
]
TELEMETRY_SUFFIX = "telemetry"   # JSON data sensor
STATUS_SUFFIX = "status"         # status akhir (teks atau JSON)
ONLINE_SUFFIX = "online"         # "online"/"offline" (Last Will ESP32), opsional

# ----------------------------------------------------------------------------
# Parameter simulasi (samakan dengan .ino)
# ----------------------------------------------------------------------------
REFERENCE_HEIGHT_CM = 100.0
MAX_LEVEL_CM = 100.0
ALERT_THRESHOLD_CM = 30.0        # NORMAL < 30
DANGER_THRESHOLD_CM = 60.0       # SIAGA 30-60, BAHAYA > 60
RAPID_RISE_THRESHOLD_CM_MIN = 5.0

STATUSES = ("NORMAL", "SIAGA", "BAHAYA", "SENSOR_ERROR")

# ----------------------------------------------------------------------------
# Perilaku dashboard
# ----------------------------------------------------------------------------
STALE_AFTER_S = 10.0       # tanpa telemetry selama ini = data ditandai LAMA
MAX_GRAPH_POINTS = 300     # batas titik grafik (+-10 menit @ 2 dtk)
MAX_LOG_LINES = 200        # batas baris event log
LOG_SAMPLE_EVERY_S = 6.0   # log baris data berkala (perubahan status selalu dicatat)
GUI_POLL_MS = 100          # seberapa sering GUI mengosongkan queue
GRAPH_REDRAW_MS = 500      # grafik digambar ulang maksimal sekali per ini
TICK_MS = 500              # pembaruan jam, umur data, dan status stale
QUEUE_MAX = 1000           # batas queue MQTT -> GUI
TIMEZONE = "Asia/Jakarta"
LOG_FILE = "floodsentinel.log"
