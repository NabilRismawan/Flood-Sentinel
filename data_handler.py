"""
data_handler.py - Validasi dan normalisasi pesan MQTT (tanpa GUI, tanpa jaringan).

Alur: pesan mentah (topic + bytes) -> validasi JSON -> ekstraksi field ->
deteksi status -> Event yang dimasukkan ke queue untuk dibaca GUI.

Prinsip: data yang tidak tersedia TIDAK diganti nilai normal secara diam-diam.
Field yang hilang/rusak menjadi None dan dicatat sebagai peringatan.

Skema telemetry yang diterima (kedua versi didukung):
  Spesifikasi M5 : status, uptime_ms, sensor_status, device_id
  .ino terbaru   : final_status, uptime_s, rapid_rise, buzzer_enabled
"""

import json
import logging
import math
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, Optional

import config

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(config.TIMEZONE)
except Exception:          # tzdata belum terpasang (umum di Windows)
    _TZ = None

log = logging.getLogger("floodsentinel.data")


def now_local() -> datetime:
    """Waktu lokal tanpa info zona (untuk tampilan dan sumbu grafik)."""
    if _TZ is None:
        return datetime.now()
    return datetime.now(_TZ).replace(tzinfo=None)


@dataclass(frozen=True)
class Telemetry:
    received_at: datetime
    base: str                            # basis topic pengirim
    device_id: Optional[str]
    distance_cm: Optional[float]
    water_level_cm: Optional[float]
    rise_rate_cm_min: Optional[float]
    base_status: Optional[str]
    status: str                          # status akhir (NORMAL/SIAGA/BAHAYA/SENSOR_ERROR/UNKNOWN)
    sensor_status: Optional[str]
    uptime_ms: Optional[float]
    rapid_rise: Optional[bool]
    warnings: tuple = ()                 # catatan kualitas data pada pesan ini


class Event(NamedTuple):
    kind: str          # "conn" | "telemetry" | "status" | "online" | "log"
    data: object
    ts: datetime


# ----------------------------------------------------------------------------
# Fungsi validasi kecil
# ----------------------------------------------------------------------------
def _to_number(value, name, warnings):
    """Angka valid -> float. null -> None. Selain itu -> None + peringatan."""
    if value is None:
        return None
    if isinstance(value, bool):
        warnings.append("%s bernilai boolean, diabaikan" % name)
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
            warnings.append("%s dikirim sebagai teks, dikonversi" % name)
        except ValueError:
            warnings.append("%s bukan angka (%r), diabaikan" % (name, value[:20]))
            return None
    else:
        warnings.append("%s bertipe %s, diabaikan" % (name, type(value).__name__))
        return None
    if not math.isfinite(number):
        warnings.append("%s tidak finit, diabaikan" % name)
        return None
    return number


def _to_status(value, name, warnings):
    """Teks status -> huruf besar. Status tidak dikenal -> 'UNKNOWN' + peringatan."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in config.STATUSES:
        return text
    warnings.append("%s tidak dikenal: %r" % (name, str(value)[:20]))
    return "UNKNOWN"


def parse_telemetry(text, base, received_at):
    """Ubah teks JSON telemetry menjadi Telemetry. Raise ValueError bila tak layak."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("bukan JSON valid (%s)" % exc.msg)
    if not isinstance(data, dict):
        raise ValueError("JSON bukan objek")

    keys = ("status", "final_status", "water_level_cm", "distance_cm")
    if not any(k in data for k in keys):
        raise ValueError("tidak lengkap: tidak ada status/water_level_cm/distance_cm")

    warnings = []
    status_raw = data.get("status", data.get("final_status"))
    status = _to_status(status_raw, "status", warnings)
    if status is None:
        warnings.append("field status tidak ada -> UNKNOWN")
        status = "UNKNOWN"

    level = _to_number(data.get("water_level_cm"), "water_level_cm", warnings)
    if level is not None and not (0.0 <= level <= config.MAX_LEVEL_CM):
        warnings.append("water_level_cm %.1f di luar 0-%.0f" % (level, config.MAX_LEVEL_CM))

    uptime = _to_number(data.get("uptime_ms"), "uptime_ms", warnings)
    if uptime is None and "uptime_s" in data:
        seconds = _to_number(data.get("uptime_s"), "uptime_s", warnings)
        uptime = None if seconds is None else seconds * 1000.0

    rapid = data.get("rapid_rise")
    rapid = rapid if isinstance(rapid, bool) else None

    device_id = data.get("device_id")
    device_id = None if device_id is None else str(device_id)[:40]
    sensor_status = data.get("sensor_status")
    sensor_status = None if sensor_status is None else str(sensor_status).strip().upper()[:20]

    return Telemetry(
        received_at=received_at,
        base=base,
        device_id=device_id,
        distance_cm=_to_number(data.get("distance_cm"), "distance_cm", warnings),
        water_level_cm=level,
        rise_rate_cm_min=_to_number(data.get("rise_rate_cm_min"), "rise_rate_cm_min", warnings),
        base_status=_to_status(data.get("base_status"), "base_status", warnings),
        status=status,
        sensor_status=sensor_status,
        uptime_ms=uptime,
        rapid_rise=rapid,
        warnings=tuple(warnings),
    )


def split_topic(topic):
    """'<basis>/<suffix>' -> (basis, suffix). Basis harus ada di config."""
    for base in sorted(config.TOPIC_BASES, key=len, reverse=True):
        if topic.startswith(base + "/"):
            return base, topic[len(base) + 1:]
    return None, None


# ----------------------------------------------------------------------------
# Pemroses pesan (dipanggil dari thread MQTT)
# ----------------------------------------------------------------------------
class MessageProcessor:
    def __init__(self, event_queue):
        self._q = event_queue
        self._lock = threading.Lock()
        self._active_base = None
        self._active_seen = None            # waktu telemetry terakhir dari basis aktif
        self._last_key = None
        self._warned_other = set()
        self._stats = {"total": 0, "telemetry": 0, "invalid": 0,
                       "duplicate": 0, "ignored": 0, "dropped": 0}

    def stats(self):
        with self._lock:
            return dict(self._stats)

    def _count(self, name):
        with self._lock:
            self._stats[name] += 1

    def emit(self, kind, data, ts=None):
        try:
            self._q.put_nowait(Event(kind, data, ts or now_local()))
        except queue.Full:
            self._count("dropped")

    def emit_log(self, level, text):
        self.emit("log", (level, text))

    def handle(self, topic, payload):
        """Proses satu pesan MQTT. Tidak pernah melempar exception."""
        try:
            self._handle(topic, payload)
        except Exception:                                # satu pesan tak boleh mematikan aplikasi
            log.exception("Gagal memproses pesan di topic %s", topic)
            self._count("invalid")
            self.emit_log("ERROR", "Kesalahan internal saat memproses pesan (lihat floodsentinel.log)")

    def _handle(self, topic, payload):
        self._count("total")
        now = now_local()
        base, suffix = split_topic(topic)
        text = payload.decode("utf-8", errors="replace").strip()

        if base is None:
            self.emit_log("WARN", "Pesan dari topic tidak dikenal: %s" % topic)
            return

        if suffix == config.TELEMETRY_SUFFIX:
            self._handle_telemetry(base, text, now)
        elif suffix == config.STATUS_SUFFIX:
            self._handle_status(base, text)
        elif suffix == config.ONLINE_SUFFIX:
            value = text.lower()
            if value in ("online", "offline"):
                self.emit("online", (base, value), now)
            else:
                self.emit_log("WARN", "Payload online tidak dikenal: %r" % text[:30])
        # suffix lain (mis. /cmd) sengaja diabaikan

    def _handle_telemetry(self, base, text, now):
        try:
            tele = parse_telemetry(text, base, now)
        except ValueError as exc:
            self._count("invalid")
            log.warning("Telemetry ditolak: %s | %r", exc, text[:100])
            self.emit_log("WARN", "Pesan telemetry ditolak: %s | %r" % (exc, text[:60]))
            return

        with self._lock:
            # Hanya satu perangkat/basis yang aktif pada satu waktu, supaya grafik tidak tercampur
            if (self._active_base is not None and base != self._active_base
                    and self._active_seen is not None
                    and (now - self._active_seen).total_seconds() <= config.STALE_AFTER_S):
                self._stats["ignored"] += 1
                first_time = base not in self._warned_other
                self._warned_other.add(base)
                ignore = True
            else:
                ignore = False
                switched = self._active_base not in (None, base)
                self._active_base = base
                self._active_seen = now
                # Deteksi pesan identik berturut-turut (duplikat)
                key = (base, tele.uptime_ms, tele.water_level_cm, tele.distance_cm,
                       tele.rise_rate_cm_min, tele.status)
                duplicate = tele.uptime_ms is not None and key == self._last_key
                self._last_key = key
                if duplicate:
                    self._stats["duplicate"] += 1
                else:
                    self._stats["telemetry"] += 1
        if ignore:
            if first_time:
                self.emit_log("WARN", "Telemetry dari basis lain diabaikan: %s (aktif: %s)"
                              % (base, self._active_base))
            return
        if duplicate:
            return
        if switched:
            self.emit_log("INFO", "Sumber data berganti ke: %s" % base)
        self.emit("telemetry", tele, now)

    def _handle_status(self, base, text):
        """Topic status bisa teks biasa ('SIAGA') atau JSON ({"status":"SIAGA"})."""
        value = text
        if text.startswith("{"):
            try:
                obj = json.loads(text)
                value = obj.get("status", obj.get("final_status", ""))
            except (json.JSONDecodeError, AttributeError):
                self._count("invalid")
                self.emit_log("WARN", "Payload status bukan JSON valid: %r" % text[:40])
                return
        warnings = []
        status = _to_status(value, "status", warnings)
        if status is None:
            self.emit_log("WARN", "Payload status kosong")
            return
        for w in warnings:
            self.emit_log("WARN", w)
        self.emit("status", (base, status))
