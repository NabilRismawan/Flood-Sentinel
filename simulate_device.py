"""
simulate_device.py - Simulator ESP32 UNTUK PENGUJIAN SAJA (bukan bagian aplikasi).

Berguna untuk menguji dashboard tanpa Wokwi. Mengirim telemetry palsu ke broker.
Data ini SIMULASI, bukan pengukuran.

Pemakaian:
  python simulate_device.py --scenario demo
  python simulate_device.py --scenario normal --base floodsentinel/sic-b8/demo01/device01
  python simulate_device.py --schema ino      # format .ino (final_status, uptime_s)

Skenario: normal | siaga | bahaya | rapid | sensor_error | dropout | invalid | demo
Pakai --host / --port untuk broker lokal (mis. mosquitto di localhost).
"""

import argparse
import json
import random
import time
import uuid

import paho.mqtt.client as mqtt

import config
from mqtt_client import _make_client

REF = config.REFERENCE_HEIGHT_CM


def status_for(level):
    if level is None:
        return "SENSOR_ERROR"
    if level < config.ALERT_THRESHOLD_CM:
        return "NORMAL"
    if level <= config.DANGER_THRESHOLD_CM:
        return "SIAGA"
    return "BAHAYA"


ESCALATE = {"NORMAL": "SIAGA", "SIAGA": "BAHAYA", "BAHAYA": "BAHAYA"}


def make_payload(schema, level, rate, uptime_ms, device_id, force_error=False):
    base_status = status_for(None if force_error else level)
    rapid = (not force_error) and rate is not None and rate >= config.RAPID_RISE_THRESHOLD_CM_MIN
    final = base_status
    if rapid and base_status in ESCALATE:
        final = ESCALATE[base_status]
    dist = None if force_error else round(REF - level, 1)
    lvl = None if force_error else round(level, 1)
    rate_v = None if (force_error or rate is None) else round(rate, 2)
    if schema == "ino":
        return {"distance_cm": dist, "water_level_cm": lvl, "rise_rate_cm_min": rate_v,
                "rapid_rise": rapid, "base_status": base_status, "final_status": final,
                "buzzer_enabled": True, "uptime_s": int(uptime_ms / 1000)}
    return {"device_id": device_id, "distance_cm": dist, "water_level_cm": lvl,
            "rise_rate_cm_min": rate_v, "base_status": base_status, "status": final,
            "sensor_status": "ERROR" if force_error else "OK", "uptime_ms": int(uptime_ms)}


def level_profile(scenario, t):
    """(level, error?) pada detik ke-t."""
    if scenario == "normal":
        return 12 + 2 * random.random(), False
    if scenario == "siaga":
        return 42 + 3 * random.random(), False
    if scenario == "bahaya":
        return 72 + 3 * random.random(), False
    if scenario == "rapid":                       # naik 8 cm/menit dari 15 cm
        return min(95.0, 15 + 8 * t / 60.0), False
    if scenario == "sensor_error":
        return 20.0, t >= 8
    if scenario == "dropout":
        return 25 + 2 * random.random(), False
    if scenario == "demo":                        # normal -> siaga -> bahaya -> turun
        cycle = t % 120
        if cycle < 25:
            return 14 + random.random(), False
        if cycle < 55:
            return 14 + (cycle - 25) * 1.5, False
        if cycle < 80:
            return 59 + (cycle - 55) * 0.6, False
        if cycle < 90:
            return 30.0, True                      # gangguan sensor singkat
        return max(12.0, 74 - (cycle - 90) * 2.0), False
    return 20.0, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="demo",
                    choices=["normal", "siaga", "bahaya", "rapid", "sensor_error", "dropout", "invalid", "demo"])
    ap.add_argument("--base", default="floodsentinel/test/sim",
                    help="basis topic (harus ada di config.TOPIC_BASES / FS_TOPIC_BASES agar terbaca dashboard)")
    ap.add_argument("--schema", choices=["spec", "ino"], default="spec")
    ap.add_argument("--host", default=config.BROKER_HOST)
    ap.add_argument("--port", type=int, default=config.BROKER_PORT)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--duration", type=float, default=0, help="detik; 0 = sampai Ctrl+C")
    args = ap.parse_args()

    client = _make_client("fs-sim-" + uuid.uuid4().hex[:8])
    client.will_set(args.base + "/online", "offline", qos=0, retain=True)
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    client.publish(args.base + "/online", "online", qos=0, retain=True)
    print("Simulator -> %s:%d  base=%s  skenario=%s  skema=%s" %
          (args.host, args.port, args.base, args.scenario, args.schema))

    start = time.time()
    prev_level, prev_t = None, None
    history = []
    try:
        while args.duration <= 0 or time.time() - start < args.duration:
            t = time.time() - start
            if args.scenario == "dropout" and 10 <= t < 25:
                time.sleep(args.interval)           # diam: simulasi telemetry berhenti
                continue
            if args.scenario == "invalid":
                bad = ['{"water_level_cm": ', "bukan json", '{"foo": 1}', "[1,2,3]",
                       json.dumps({"status": "NORMAL", "water_level_cm": "abc", "distance_cm": 80}),
                       json.dumps({"status": "HEBAT", "water_level_cm": 20, "distance_cm": 80})]
                payload = bad[int(t / args.interval) % len(bad)]
                client.publish(args.base + "/telemetry", payload, 0)
                print("kirim:", payload)
                time.sleep(args.interval)
                continue

            level, err = level_profile(args.scenario, t)
            history.append((t, level))
            history = [h for h in history if t - h[0] <= 20]
            rate = None
            if not err and len(history) >= 4 and t - history[0][0] >= 8:
                rate = (level - history[0][1]) / (t - history[0][0]) * 60.0
            payload = make_payload(args.schema, level, rate, t * 1000, "device01", err)
            client.publish(args.base + "/telemetry", json.dumps(payload), 0)
            client.publish(args.base + "/status", payload.get("status", payload.get("final_status")), 0, retain=True)
            print("t=%5.1fs  %s" % (t, payload))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(args.base + "/online", "offline", qos=0, retain=True)
        time.sleep(0.3)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
