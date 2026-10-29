"""
mqtt_test.py - Step 3: uji konektivitas MQTT TANPA GUI.

Pemakaian:
  python mqtt_test.py              dengarkan data dari ESP32 (Wokwi) selama 30 detik
  python mqtt_test.py --seconds 60
  python mqtt_test.py --selftest   kirim pesan ke diri sendiri (cek broker bisa dipakai)

Keluar dengan kode 0 jika berhasil, 1 jika gagal.
"""

import argparse
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

import config
from mqtt_client import _make_client, all_topics


def run(seconds, selftest):
    client_id = "fs-test-" + uuid.uuid4().hex[:8]
    client = _make_client(client_id)
    connected = threading.Event()
    got = []
    test_topic = "floodsentinel/test/%s" % uuid.uuid4().hex[:8]

    def on_connect(c, u, flags, rc, props=None):
        failed = rc.is_failure if hasattr(rc, "is_failure") else rc != 0
        if failed:
            print("[GAGAL] broker menolak koneksi: %s" % rc)
            return
        print("[OK] terhubung ke %s:%d (client id %s)" % (config.BROKER_HOST, config.BROKER_PORT, client_id))
        if selftest:
            c.subscribe(test_topic, config.QOS)
        else:
            c.subscribe(all_topics())
            for t, _ in all_topics():
                print("     subscribe:", t)
        connected.set()

    def on_message(c, u, msg):
        text = msg.payload.decode("utf-8", errors="replace")
        got.append((msg.topic, text))
        print("[PESAN] %s -> %s" % (msg.topic, text[:200]))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.BROKER_HOST, config.BROKER_PORT, keepalive=config.KEEPALIVE_S)
    client.loop_start()
    try:
        if not connected.wait(15):
            print("[GAGAL] tidak bisa terhubung ke %s:%d dalam 15 detik." % (config.BROKER_HOST, config.BROKER_PORT))
            print("        Cek internet, host/port di config.py, dan firewall.")
            return 1
        if selftest:
            time.sleep(1)                       # beri waktu subscribe selesai
            client.publish(test_topic, "ping", config.QOS)
            deadline = time.time() + 10
            while time.time() < deadline and not got:
                time.sleep(0.2)
            if got:
                print("[OK] selftest: publish dan subscribe lewat broker berhasil.")
                return 0
            print("[GAGAL] selftest: pesan tidak kembali dalam 10 detik.")
            return 1
        print("Menunggu pesan %d detik... (jalankan simulasi Wokwi sekarang)" % seconds)
        time.sleep(seconds)
        if got:
            print("[OK] %d pesan diterima." % len(got))
            return 0
        print("[PERINGATAN] terhubung ke broker, tapi tidak ada pesan. Periksa:")
        print("  - simulasi Wokwi sedang berjalan dan sudah terhubung WiFi/MQTT")
        print("  - topic di .ino sama dengan config.TOPIC_BASES: %s" % config.TOPIC_BASES)
        return 1
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Uji konektivitas MQTT FloodSentinel")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.seconds, args.selftest))
