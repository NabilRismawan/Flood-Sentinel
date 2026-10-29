# FloodSentinel – Python Monitoring Dashboard (Milestone 5)

Dashboard desktop (Tkinter + Matplotlib + paho-mqtt) yang menampilkan data **simulasi** sensor
ketinggian air dari ESP32 di Wokwi lewat broker MQTT publik HiveMQ.

> **Catatan jujur:** semua angka berasal dari simulasi Wokwi. Batas NORMAL/SIAGA/BAHAYA
> (30 cm dan 60 cm) serta ambang naik cepat (5 cm/menit) adalah **parameter simulasi**,
> bukan standar keselamatan banjir, dan sistem ini **belum divalidasi di lapangan**.

---

## 1. Step 1 – Architecture Review (kecocokan dengan Milestone 4)

Dokumen Milestone 5 dan sketch `.ino` (Milestone 4) ternyata **tidak identik**:

| Hal | Dokumen Milestone 5 | `.ino` yang sedang dipakai | Solusi di dashboard |
|---|---|---|---|
| Basis topic | `floodsentinel/sic-b8/demo01/device01` | `floodsentinel/13223072` | Dashboard mendengarkan **kedua** basis (`config.TOPIC_BASES`) |
| Field status | `status` | `final_status` | Menerima keduanya |
| Uptime | `uptime_ms` | `uptime_s` | Menerima keduanya (detik dikonversi) |
| `sensor_status`, `device_id` | ada | tidak ada | Opsional; kalau kosong ditampilkan "tidak dikirim" |
| Nilai tidak tersedia | – | `null` | Menjadi "n/a", **tidak** diganti 0 atau nilai normal |
| Topic tambahan | `/status` | `/status`, `/online` (Last Will), `/cmd` | `/online` dipakai, `/cmd` diabaikan |

**Rekomendasi:** setelah tim sepakat, samakan topic di `.ino` dan hapus basis yang tidak dipakai
di `config.py`. Hanya satu basis yang diproses pada satu waktu supaya grafik tidak tercampur.

Dua hal lain yang perlu diketahui:
- Broker publik HiveMQ **tanpa login**: siapa pun yang tahu topic bisa mengirim data atau perintah.
  Karena itu dashboard ini sengaja **tidak punya tombol kontrol** (hanya membaca).
- Rubrik SIC menyebut *HiveMQ Cloud*. Broker publik sudah cukup untuk demo, tetapi bila ingin
  memakai HiveMQ Cloud, ubah host/port di `config.py` dan tambahkan TLS + akun (port 8883).

---

## 2. Arsitektur

```
 Wokwi ESP32 ──publish──►  HiveMQ broker  ──subscribe──►  Thread paho-mqtt
 (HC-SR04)                 broker.hivemq.com:1883              │
                                                               ▼
                                              MessageProcessor (data_handler.py)
                                              validasi JSON, ekstraksi, deteksi status
                                                               │  queue.Queue
                                                               ▼
                                              Thread utama Tkinter (dashboard.py)
                                              root.after() mengosongkan queue
                                              → panel, grafik Matplotlib, event log
```

Aturan yang dijaga:
1. Callback paho **tidak pernah** menyentuh widget Tkinter.
2. Data pindah thread hanya lewat `queue.Queue`.
3. GUI memakai `root.after()`; tidak ada `time.sleep()` di thread GUI.
4. **CONNECTED ≠ data masuk.** Status broker dan status data (LIVE / STALE / NO DATA) ditampilkan terpisah.

### Diagram alur MQTT

```
ESP32 ── telemetry (JSON, tiap ~2 dtk) ──► <basis>/telemetry ─┐
ESP32 ── status (retained)             ──► <basis>/status    ─┼─► Dashboard (QoS 0)
ESP32 ── Last Will "offline"/"online"  ──► <basis>/online    ─┘
Dashboard ── subscribe saat on_connect (diulang otomatis setiap reconnect)
```

### Struktur folder

```
FloodSentinel/
├── main.py             titik masuk, logging, siklus hidup aplikasi
├── config.py           semua pengaturan (broker, topic, ambang, batas)
├── mqtt_client.py      koneksi paho di thread sendiri, reconnect otomatis
├── data_handler.py     validasi JSON, normalisasi, deteksi duplikat (tanpa GUI)
├── dashboard.py        seluruh tampilan Tkinter + grafik Matplotlib
├── mqtt_test.py        Step 3: uji koneksi MQTT tanpa GUI
├── simulate_device.py  simulator ESP32 untuk pengujian (bukan bagian aplikasi)
├── requirements.txt
└── README.md
```

---

## 3. Instalasi (Step 2 – Environment)

Butuh Python 3.9+ (Tkinter ikut terpasang di Windows/macOS; di Linux: `sudo apt install python3-tk`).

```bash
cd FloodSentinel
python -m venv venv
# Windows:  venv\Scripts\activate        Linux/macOS:  source venv/bin/activate
pip install -r requirements.txt
```

Cek cepat: `python -c "import tkinter, paho, matplotlib; print('OK')"`

## 4. Menjalankan

**Step 3 – tes koneksi dulu (tanpa GUI):**
```bash
python mqtt_test.py --selftest      # broker bisa dipakai? (kirim ke diri sendiri)
python mqtt_test.py --seconds 30    # dengarkan data dari Wokwi
```

**Step 4 – jalankan dashboard:**
```bash
python main.py
```
Log ikut tersimpan di `floodsentinel.log`.

### Menghubungkan dengan Wokwi
1. Buka proyek Wokwi dan jalankan simulasi sampai Serial Monitor menampilkan WiFi dan MQTT terhubung.
2. Pastikan di `.ino`: broker `broker.hivemq.com`, port `1883`, dan **topic sama** dengan salah satu basis di `config.py`.
3. Jalankan `python mqtt_test.py`; pesan telemetry harus muncul.
4. Jalankan `python main.py`. Banner oranye "Menunggu telemetry" hilang saat data pertama tiba.
5. Wokwi harus tetap berjalan (tab terbuka); simulasi berhenti ketika tab ditutup.

### Mengubah pengaturan tanpa mengedit file
```bash
# contoh: broker lokal dan topic sendiri
FS_BROKER_HOST=127.0.0.1 FS_BROKER_PORT=1883 FS_TOPIC_BASES=floodsentinel/13223072 python main.py
```
(Windows PowerShell: `$env:FS_BROKER_HOST="127.0.0.1"; python main.py`)

---

## 5. Penjelasan modul

- **config.py** – satu tempat untuk broker, topic, ambang, batas titik grafik, dan batas stale.
- **data_handler.py** – `parse_telemetry()` memvalidasi JSON; field hilang/rusak menjadi `None` + catatan
  peringatan (tidak pernah diganti nilai normal). Status tak dikenal → `UNKNOWN`. `MessageProcessor`
  menghitung statistik, membuang pesan duplikat beruntun, dan mengabaikan basis topic lain selagi satu basis aktif.
- **mqtt_client.py** – `MqttWorker`: ID klien unik tiap dijalankan (ID yang sama membuat klien lain terputus),
  `connect_async` + `loop_start`, backoff reconnect 1–15 detik, subscribe ulang di `on_connect`.
- **dashboard.py** – panel Connection, Water Level (tangki + garis ambang), Warning Status, Rate of Rise,
  grafik Matplotlib (maks. 300 titik, digambar ulang paling sering 2× per detik), dan Event Log (maks. 200 baris).
  Jika telemetry berhenti > 10 detik: banner oranye, tampilan diredupkan, tulisan "DATA TIDAK DIPERBARUI" di grafik.
- **main.py** – merangkai semuanya; menutup koneksi dengan rapi saat jendela ditutup.

Arti status: **NORMAL** hijau (< 30 cm) · **SIAGA** kuning (30–60 cm, atau naik cepat dari NORMAL) ·
**BAHAYA** merah (> 60 cm, atau naik cepat dari SIAGA) · **SENSOR_ERROR** ungu (ketinggian tidak diketahui).
Dashboard **mengikuti status yang dikirim ESP32**, tidak menghitung ulang sendiri.

---

## 6. Prosedur pengujian

Alat bantu: `simulate_device.py` (tanpa Wokwi) atau Wokwi sendiri.
Untuk simulator, dashboard harus mendengarkan basis yang sama, mis.:
```bash
export FS_TOPIC_BASES=floodsentinel/test/sim        # terminal 1
python main.py
python simulate_device.py --scenario normal         # terminal 2 (tambah --host/--port untuk broker lain)
```

| # | Pengujian | Tujuan | Langkah | Hasil yang diharapkan | Kriteria lulus |
|---|---|---|---|---|---|
| 1 | Normal | Status NORMAL tampil benar | `--scenario normal` (atau Wokwi: air < 30 cm) | Kotak hijau NORMAL, grafik di zona hijau, feed LIVE | Level < 30 cm, status hijau, tanpa peringatan |
| 2 | Warning (SIAGA) | Status SIAGA tampil | `--scenario siaga` (30–60 cm) | Kotak kuning SIAGA, log mencatat perubahan status | Status kuning, perubahan tercatat di Event Log |
| 3 | Danger (BAHAYA) | Status BAHAYA tampil | `--scenario bahaya` (> 60 cm) | Kotak merah BAHAYA, zona tangki merah | Status merah sesuai payload |
| 4 | Rapid rise | Kenaikan cepat terdeteksi | `--scenario rapid` | Rate ≥ 5 cm/mnt merah, penanda ▲ merah di grafik, log "KENAIKAN CEPAT", status naik satu tingkat | Log + penanda muncul; status mengikuti payload |
| 5 | MQTT disconnection | Gangguan jaringan ditangani | Jalankan normal, lalu matikan internet/broker 20 dtk, lalu nyalakan lagi | Broker → RECONNECTING; setelah ~10 dtk banner "DATA TIDAK DIPERBARUI" dan tampilan diredupkan (jangan tampak aman); setelah pulih → CONNECTED + data LIVE, dan tanpa restart | Tidak crash; nilai lama jelas ditandai basi; data kembali sendiri |
| 6 | Sensor error | Sensor gagal tidak tampak "aman" | `--scenario sensor_error` (nilai `null`) | Kotak ungu SENSOR_ERROR, level "-- cm", rate "tidak tersedia", titik grafik terputus | Tidak ada nilai 0/normal palsu |

Tambahan: `--scenario invalid` (JSON rusak, field hilang, nilai bukan angka, status tak dikenal) →
Event Log mencatat penolakan, aplikasi tetap berjalan; `--scenario dropout` → STALE lalu pulih; `--schema ino` →
format payload `.ino` (`final_status`, `uptime_s`).

---

## 7. Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| `ModuleNotFoundError: tkinter` | Linux: `sudo apt install python3-tk`. Windows/macOS: pasang Python dari python.org |
| Broker "RECONNECTING" terus | Tidak ada internet, port 1883 diblokir jaringan kampus/firewall, atau host salah. Coba `python mqtt_test.py --selftest`, atau hotspot HP |
| Broker CONNECTED tapi "Menunggu telemetry" | Wokwi belum jalan, atau **topic berbeda**. Samakan dengan `config.TOPIC_BASES`; cek dengan `mqtt_test.py` |
| Dashboard sering terputus sendiri | Dua klien memakai Client ID sama. Dashboard memakai ID acak; ubah Client ID di `.ino` bila sama dengan milik orang lain |
| Pesan "Telemetry dari basis lain diabaikan" | Dua perangkat aktif bersamaan; ini disengaja. Matikan salah satu |
| Pesan "ditolak" di Event Log | Payload bukan JSON valid / tidak lengkap. Periksa Serial Monitor ESP32 |
| Rate "belum tersedia" di awal | ESP32 butuh jendela pengukuran (± 16 detik) sebelum menghitung laju |
| Grafik tidak bergerak | Data STALE (lihat banner), atau jendela tidak terlihat; periksa feed di panel Connection |
| Data orang lain muncul | Broker publik; basis topic Anda mungkin dipakai pihak lain. Pakai topic yang unik |
| Tidak bisa membuka jendela di server/WSL | Perlu layar: jalankan di desktop, atau `xvfb-run python main.py` untuk uji otomatis |

## 8. Keterbatasan
- Simulasi Wokwi; tidak ada pengujian lapangan, kalibrasi sensor, atau validasi ambang banjir.
- Broker publik tanpa autentikasi/enkripsi (QoS 0: pesan bisa hilang saat jaringan buruk).
- Dashboard hanya membaca; tidak mengirim perintah ke perangkat.
