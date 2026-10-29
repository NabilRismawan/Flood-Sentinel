"""
mqtt_client.py - Koneksi MQTT (paho-mqtt) yang berjalan di thread sendiri.

Thread MQTT TIDAK menyentuh Tkinter. Semua hasil dikirim ke GUI lewat
queue.Queue (diisi oleh MessageProcessor dan oleh kelas ini untuk status koneksi).

Status koneksi yang dilaporkan:
  CONNECTED     terhubung ke broker
  RECONNECTING  terputus / gagal tersambung, paho sedang mencoba lagi otomatis
  DISCONNECTED  dihentikan dengan sengaja (aplikasi ditutup)

Catatan: CONNECTED hanya berarti broker terjangkau. Apakah telemetry masih
masuk dinilai terpisah oleh dashboard (LIVE / STALE).
"""

import logging
import threading
import uuid

import paho.mqtt.client as mqtt

import config
from data_handler import MessageProcessor

log = logging.getLogger("floodsentinel.mqtt")

CONNECTED = "CONNECTED"
RECONNECTING = "RECONNECTING"
DISCONNECTED = "DISCONNECTED"


def _make_client(client_id):
    """Kompatibel dengan paho-mqtt 2.x dan 1.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def all_topics():
    """Daftar (topic, qos) yang di-subscribe: telemetry, status, online per basis."""
    suffixes = (config.TELEMETRY_SUFFIX, config.STATUS_SUFFIX, config.ONLINE_SUFFIX)
    return [("%s/%s" % (base, s), config.QOS) for base in config.TOPIC_BASES for s in suffixes]


class MqttWorker:
    def __init__(self, processor: MessageProcessor):
        self._processor = processor
        self._client = None
        self._stopping = False
        self._lock = threading.Lock()
        self._state = DISCONNECTED
        self.client_id = "fs-dash-" + uuid.uuid4().hex[:8]   # unik tiap aplikasi dijalankan

    @property
    def state(self):
        with self._lock:
            return self._state

    def _set_state(self, state, detail=""):
        with self._lock:
            changed = state != self._state
            self._state = state
        if changed or detail:
            self._processor.emit("conn", (state, detail))

    # ------------------------------------------------------------ callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        failed = (reason_code.is_failure if hasattr(reason_code, "is_failure")
                  else reason_code != 0)
        if failed:
            log.warning("Koneksi ditolak broker: %s", reason_code)
            self._set_state(RECONNECTING, "ditolak: %s" % reason_code)
            return
        # Subscribe dilakukan di sini supaya otomatis diulang setiap reconnect
        client.subscribe(all_topics())
        log.info("Terhubung, subscribe: %s", [t for t, _ in all_topics()])
        self._set_state(CONNECTED, "%s:%d" % (config.BROKER_HOST, config.BROKER_PORT))

    def _on_disconnect(self, client, userdata, *args):
        if self._stopping:
            self._set_state(DISCONNECTED)
        else:
            log.warning("Koneksi terputus, paho mencoba menyambung kembali")
            self._set_state(RECONNECTING, "koneksi terputus")

    def _on_connect_fail(self, client, userdata, *args):
        log.warning("Gagal tersambung ke %s:%d", config.BROKER_HOST, config.BROKER_PORT)
        self._set_state(RECONNECTING, "gagal tersambung ke broker")

    def _on_message(self, client, userdata, msg):
        self._processor.handle(msg.topic, bytes(msg.payload))

    # ------------------------------------------------------------ kontrol
    def start(self):
        """Mulai di thread latar belakang. Tidak memblokir, tidak melempar error jaringan."""
        if self._client is not None:
            return
        self._stopping = False
        client = _make_client(self.client_id)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_connect_fail = self._on_connect_fail
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=15)
        self._client = client
        self._set_state(RECONNECTING, "menghubungkan ke %s:%d" % (config.BROKER_HOST, config.BROKER_PORT))
        client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=config.KEEPALIVE_S)
        client.loop_start()                      # thread paho: koneksi, reconnect, callback

    def stop(self):
        self._stopping = True
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
        self._set_state(DISCONNECTED)
