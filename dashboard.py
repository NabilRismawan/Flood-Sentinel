"""
dashboard.py - Tampilan Tkinter FloodSentinel (hanya GUI, tanpa logika jaringan).

Aturan thread:
  * Callback MQTT tidak pernah menyentuh widget Tkinter.
  * Data datang lewat queue.Queue, dikosongkan oleh root.after() di thread GUI.
  * Tidak ada time.sleep() di thread GUI.

Bedakan dua hal:
  * Status koneksi BROKER (CONNECTED / RECONNECTING / DISCONNECTED)
  * Status aliran DATA (LIVE / STALE / NO DATA)
    CONNECTED tidak menjamin telemetry masih masuk.
"""

import logging
import math
import queue
import time
import tkinter as tk
from collections import deque

import matplotlib
matplotlib.use("TkAgg")                      # sebelum import backend lain
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import config
import mqtt_client
from data_handler import now_local

log = logging.getLogger("floodsentinel.gui")

# ----------------------------------------------------------------------------
# Tema
# ----------------------------------------------------------------------------
FONT = "Helvetica"
BG = "#0b1220"
PANEL = "#162033"
BORDER = "#2b3a55"
TEXT = "#e2e8f0"
MUTED = "#8aa0bf"
GREEN = "#22c55e"
YELLOW = "#facc15"
RED = "#ef4444"
PURPLE = "#a855f7"
ORANGE = "#fb923c"
BLUE = "#38bdf8"
GRAY = "#64748b"

# status -> (warna kotak, warna teks, penjelasan)
STATUS_STYLE = {
    "NORMAL": (GREEN, "#052e16",
               "Ketinggian air di bawah %.0f cm. Kondisi aman (parameter simulasi)."
               % config.ALERT_THRESHOLD_CM),
    "SIAGA": (YELLOW, "#422006",
              "Ketinggian %.0f-%.0f cm, atau naik cepat dari NORMAL. Tingkatkan pemantauan."
              % (config.ALERT_THRESHOLD_CM, config.DANGER_THRESHOLD_CM)),
    "BAHAYA": (RED, "#ffffff",
               "Ketinggian di atas %.0f cm, atau naik cepat dari SIAGA. Peringatan tertinggi."
               % config.DANGER_THRESHOLD_CM),
    "SENSOR_ERROR": (PURPLE, "#ffffff",
                     "Sensor tidak memberi bacaan valid. Ketinggian air TIDAK diketahui, "
                     "jangan dianggap aman."),
    "UNKNOWN": (GRAY, "#ffffff",
                "Status dari perangkat tidak dikenal. Periksa payload (lihat Event Log)."),
    "WAITING": (GRAY, "#ffffff",
                "Belum ada telemetry. Pastikan simulasi Wokwi berjalan dan topic-nya sama."),
}

CONN_COLORS = {mqtt_client.CONNECTED: GREEN, mqtt_client.RECONNECTING: ORANGE,
               mqtt_client.DISCONNECTED: RED}
FEED_COLORS = {"LIVE": GREEN, "STALE": ORANGE, "NO DATA": GRAY}
LOG_TAG_COLORS = {"info": TEXT, "ok": GREEN, "warn": ORANGE, "error": RED,
                  "NORMAL": GREEN, "SIAGA": YELLOW, "BAHAYA": RED,
                  "SENSOR_ERROR": PURPLE, "UNKNOWN": GRAY}


def _blend(c1, c2, t):
    """Campur dua warna hex. t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _fmt_age(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%d dtk" % seconds
    return "%d mnt %d dtk" % divmod(seconds, 60)


class Dashboard:
    def __init__(self, root, event_queue, processor, worker):
        self.root = root
        self.q = event_queue
        self.processor = processor
        self.worker = worker

        # ---- state tampilan
        self.latest = None                    # Telemetry terakhir
        self.latest_mono = None               # time.monotonic() saat diterima
        self.conn_state = worker.state
        self.conn_detail = ""
        self.device_online = None
        self.status_topic = None
        self.feed_state = "NO DATA"
        self.history = deque(maxlen=config.MAX_GRAPH_POINTS)   # (tanggal-num, level|nan, rapid)
        self.graph_dirty = True
        self._prev_status = None
        self._prev_rapid = False
        self._last_sample_log = 0.0
        self._warn_seen = set()

        root.title("FloodSentinel - Water Level Monitoring Dashboard")
        root.configure(bg=BG)
        root.geometry("1280x860")
        root.minsize(1100, 780)

        self._build_ui()
        self._init_graph()
        self._render_all()
        self._log("Dashboard dimulai. Broker: %s:%d" % (config.BROKER_HOST, config.BROKER_PORT), "info")
        self._log("Topic dipantau: %s" % ", ".join(t for t, _ in mqtt_client.all_topics()[::3]), "info")

        self.root.after(config.GUI_POLL_MS, self._poll_queue)
        self.root.after(config.GRAPH_REDRAW_MS, self._graph_tick)
        self.root.after(config.TICK_MS, self._tick)

    # ======================================================================
    # Membangun UI
    # ======================================================================
    def _panel(self, parent, title):
        outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(outer, text=title, bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        inner = tk.Frame(outer, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        return outer, inner

    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(2, weight=1)

        # ---- header
        header = tk.Frame(root, bg=BG)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 2))
        tk.Label(header, text="FLOODSENTINEL", bg=BG, fg=TEXT,
                 font=(FONT, 20, "bold")).pack(side="left")
        tk.Label(header, text="  Water Level Monitoring  |  Simulasi Wokwi + MQTT",
                 bg=BG, fg=MUTED, font=(FONT, 11)).pack(side="left", pady=(7, 0))
        self.clock = tk.Label(header, text="", bg=BG, fg=MUTED, font=(FONT, 11))
        self.clock.pack(side="right", pady=(7, 0))

        # ---- banner peringatan data lama (disembunyikan saat LIVE)
        self.banner = tk.Label(root, text="", bg=ORANGE, fg="#1c1917", font=(FONT, 11, "bold"),
                               anchor="w", padx=12, pady=5)
        self.banner.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 2))

        # ---- kolom kiri
        left = tk.Frame(root, bg=BG)
        left.grid(row=2, column=0, sticky="ns", padx=(14, 7), pady=(4, 4))
        self._build_connection_panel(left)
        self._build_water_panel(left)

        # ---- kolom kanan
        right = tk.Frame(root, bg=BG)
        right.grid(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(4, 4))
        right.columnconfigure(0, weight=3, uniform="cards")
        right.columnconfigure(1, weight=2, uniform="cards")
        right.rowconfigure(1, weight=3)
        right.rowconfigure(2, weight=2)
        self._build_status_panel(right)
        self._build_rate_panel(right)
        self._build_graph_panel(right)
        self._build_log_panel(right)

        # ---- footer
        tk.Label(root, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
                 text=("Batas ketinggian adalah PARAMETER SIMULASI (NORMAL <%.0f | SIAGA %.0f-%.0f | "
                       "BAHAYA >%.0f cm; naik cepat >= %.0f cm/menit), bukan standar keselamatan banjir."
                       % (config.ALERT_THRESHOLD_CM, config.ALERT_THRESHOLD_CM,
                          config.DANGER_THRESHOLD_CM, config.DANGER_THRESHOLD_CM,
                          config.RAPID_RISE_THRESHOLD_CM_MIN))
                 ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

    def _kv_row(self, parent, row, label):
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=(FONT, 10),
                 anchor="w").grid(row=row, column=0, sticky="w", pady=2)
        value = tk.Label(parent, text="-", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"),
                         anchor="w", justify="left", wraplength=190)
        value.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=2)
        return value

    def _build_connection_panel(self, parent):
        outer, inner = self._panel(parent, "CONNECTION")
        outer.pack(fill="x", pady=(0, 8))
        self.v_broker = self._kv_row(inner, 0, "MQTT broker")

        tk.Label(inner, text="Broker status", bg=PANEL, fg=MUTED, font=(FONT, 10),
                 anchor="w").grid(row=1, column=0, sticky="w", pady=2)
        box = tk.Frame(inner, bg=PANEL)
        box.grid(row=1, column=1, sticky="w", padx=(10, 0))
        self.conn_dot = tk.Canvas(box, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.conn_dot.pack(side="left")
        self.conn_dot_id = self.conn_dot.create_oval(2, 2, 12, 12, fill=GRAY, outline="")
        self.v_conn = tk.Label(box, text="-", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"))
        self.v_conn.pack(side="left", padx=(6, 0))

        self.v_feed = self._kv_row(inner, 2, "Data feed")
        self.v_device = self._kv_row(inner, 3, "Device ID")
        self.v_lwt = self._kv_row(inner, 4, "Device (LWT)")
        self.v_topic = self._kv_row(inner, 5, "Topic aktif")
        self.v_last = self._kv_row(inner, 6, "Last message")
        self.v_total = self._kv_row(inner, 7, "Total messages")
        self.v_broker.configure(text="%s:%d" % (config.BROKER_HOST, config.BROKER_PORT))

    def _build_water_panel(self, parent):
        outer, inner = self._panel(parent, "WATER LEVEL")
        outer.pack(fill="both", expand=True)
        self.v_level = tk.Label(inner, text="-- cm", bg=PANEL, fg=BLUE, font=(FONT, 40, "bold"),
                                anchor="w")
        self.v_level.grid(row=0, column=0, columnspan=2, sticky="w")

        details = tk.Frame(inner, bg=PANEL)
        details.grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.v_dist = self._kv_row_small(details, 0, "Sensor distance")
        self._kv_row_small(details, 1, "Reference height").configure(
            text="%.0f cm" % config.REFERENCE_HEIGHT_CM)
        self._kv_row_small(details, 2, "Max sim. level").configure(
            text="%.0f cm" % config.MAX_LEVEL_CM)
        self.v_zone = self._kv_row_small(details, 3, "Zona ketinggian")

        self.tank = tk.Canvas(inner, width=110, height=250, bg=PANEL, highlightthickness=0)
        self.tank.grid(row=1, column=1, sticky="ne", padx=(14, 0), pady=(6, 0))

    def _kv_row_small(self, parent, row, label):
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=(FONT, 9),
                 anchor="w").grid(row=row * 2, column=0, sticky="w")
        value = tk.Label(parent, text="-", bg=PANEL, fg=TEXT, font=(FONT, 12, "bold"), anchor="w")
        value.grid(row=row * 2 + 1, column=0, sticky="w", pady=(0, 6))
        return value

    def _build_status_panel(self, parent):
        outer, inner = self._panel(parent, "WARNING STATUS")
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        self.status_box = tk.Label(inner, text="--", font=(FONT, 34, "bold"), pady=8)
        self.status_box.pack(fill="x")
        self.v_status_meta = tk.Label(inner, text="", bg=PANEL, fg=MUTED, font=(FONT, 10),
                                      anchor="w", justify="left")
        self.v_status_meta.pack(fill="x", pady=(8, 0))
        self.v_status_desc = tk.Label(inner, text="", bg=PANEL, fg=TEXT, font=(FONT, 11),
                                      anchor="w", justify="left", wraplength=520)
        self.v_status_desc.pack(fill="x", pady=(4, 6))

        legend = tk.Frame(inner, bg=PANEL)
        legend.pack(fill="x")
        items = [("NORMAL", GREEN, "<%.0f" % config.ALERT_THRESHOLD_CM),
                 ("SIAGA", YELLOW, "%.0f-%.0f" % (config.ALERT_THRESHOLD_CM, config.DANGER_THRESHOLD_CM)),
                 ("BAHAYA", RED, ">%.0f" % config.DANGER_THRESHOLD_CM),
                 ("SENSOR_ERROR", PURPLE, "sensor gagal")]
        for i, (name, color, rng) in enumerate(items):
            tk.Label(legend, text="  ", bg=color).grid(row=0, column=i * 2, padx=(0 if i == 0 else 10, 4))
            tk.Label(legend, text="%s %s" % (name, rng), bg=PANEL, fg=MUTED,
                     font=(FONT, 9)).grid(row=0, column=i * 2 + 1)

    def _build_rate_panel(self, parent):
        outer, inner = self._panel(parent, "RATE OF RISE")
        outer.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        self.v_rate = tk.Label(inner, text="-- cm/min", bg=PANEL, fg=TEXT,
                               font=(FONT, 30, "bold"), anchor="w")
        self.v_rate.pack(fill="x")
        self.v_rate_note = tk.Label(inner, text="", bg=PANEL, fg=MUTED, font=(FONT, 10),
                                    anchor="w", justify="left", wraplength=360)
        self.v_rate_note.pack(fill="x", pady=(2, 6))
        self.v_rapid = tk.Label(inner, text="", bg=PANEL, fg=MUTED, font=(FONT, 11, "bold"),
                                anchor="w")
        self.v_rapid.pack(fill="x")
        tk.Label(inner, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=360,
                 text=("Nilai ini adalah laju perubahan ketinggian air (cm per menit), "
                       "BUKAN prediksi waktu terjadinya banjir. Ambang naik cepat %.0f cm/menit "
                       "adalah parameter simulasi." % config.RAPID_RISE_THRESHOLD_CM_MIN)
                 ).pack(fill="x", pady=(8, 0))

    def _build_graph_panel(self, parent):
        outer, inner = self._panel(parent, "REAL-TIME WATER LEVEL GRAPH")
        outer.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        self.graph_frame = inner

    def _build_log_panel(self, parent):
        outer, inner = self._panel(parent, "EVENT LOG")
        outer.grid(row=2, column=0, columnspan=2, sticky="nsew")
        scroll = tk.Scrollbar(inner)
        scroll.pack(side="right", fill="y")
        self.log_text = tk.Text(inner, height=7, bg="#0b1220", fg=TEXT, font=("Courier", 10),
                                relief="flat", wrap="none", state="disabled",
                                yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.log_text.yview)
        for tag, color in LOG_TAG_COLORS.items():
            self.log_text.tag_configure(tag, foreground=color)

    # ======================================================================
    # Grafik Matplotlib
    # ======================================================================
    def _init_graph(self):
        self.fig = Figure(figsize=(7, 2.8), dpi=100, facecolor=PANEL)
        ax = self.ax = self.fig.add_subplot(111, facecolor=BG)
        self.fig.subplots_adjust(left=0.07, right=0.96, top=0.95, bottom=0.2)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.grid(True, color=BORDER, alpha=0.7, linewidth=0.6)
        ax.set_ylim(0, config.MAX_LEVEL_CM + 5)
        ax.set_ylabel("Ketinggian air (cm)", color=MUTED, fontsize=9)
        ax.set_xlabel("Waktu", color=MUTED, fontsize=9)
        ax.xaxis_date()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

        top = config.MAX_LEVEL_CM + 5
        ax.axhspan(0, config.ALERT_THRESHOLD_CM, color=GREEN, alpha=0.10, lw=0)
        ax.axhspan(config.ALERT_THRESHOLD_CM, config.DANGER_THRESHOLD_CM, color=YELLOW, alpha=0.10, lw=0)
        ax.axhspan(config.DANGER_THRESHOLD_CM, top, color=RED, alpha=0.10, lw=0)
        for value, color, label in ((config.ALERT_THRESHOLD_CM, YELLOW,
                                     "SIAGA %.0f cm" % config.ALERT_THRESHOLD_CM),
                                    (config.DANGER_THRESHOLD_CM, RED,
                                     "BAHAYA %.0f cm" % config.DANGER_THRESHOLD_CM)):
            ax.axhline(value, color=color, linestyle="--", linewidth=1)
            ax.text(0.006, value + 1, label, transform=ax.get_yaxis_transform(),
                    color=color, fontsize=8, va="bottom")

        self.line, = ax.plot([], [], color=BLUE, linewidth=1.8, marker="o", markersize=3,
                             label="Ketinggian air")
        self.rapid_pts, = ax.plot([], [], linestyle="none", marker="^", color=RED, markersize=7,
                                  label="Naik cepat")
        self.stale_text = ax.text(0.5, 0.5, "DATA TIDAK DIPERBARUI", transform=ax.transAxes,
                                  ha="center", va="center", color=ORANGE, fontsize=18,
                                  fontweight="bold", alpha=0.85, visible=False)
        leg = ax.legend(loc="upper left", fontsize=8, facecolor=PANEL, edgecolor=BORDER,
                        labelcolor=TEXT, bbox_to_anchor=(0.0, 0.8))
        leg.get_frame().set_alpha(0.9)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _graph_tick(self):
        try:
            if self.graph_dirty:
                self.graph_dirty = False
                self._redraw_graph()
        except Exception:
            log.exception("Gagal menggambar grafik")
        self.root.after(config.GRAPH_REDRAW_MS, self._graph_tick)

    def _redraw_graph(self):
        xs = [h[0] for h in self.history]
        ys = [h[1] for h in self.history]
        self.line.set_data(xs, ys)                       # nan = putus garis (data tidak ada)
        rx = [h[0] for h in self.history if h[2] and not math.isnan(h[1])]
        ry = [h[1] for h in self.history if h[2] and not math.isnan(h[1])]
        self.rapid_pts.set_data(rx, ry)

        minimum_span = 60.0 / 86400.0                    # jendela minimal 60 detik
        if xs:
            span = max(minimum_span, xs[-1] - xs[0])
            self.ax.set_xlim(xs[-1] - span, xs[-1] + span * 0.02)
        else:
            now = mdates.date2num(now_local())
            self.ax.set_xlim(now - minimum_span, now)
        self.stale_text.set_visible(self.latest is not None and self.feed_state != "LIVE")
        self.canvas.draw_idle()

    # ======================================================================
    # Event dari queue (MQTT thread -> GUI thread)
    # ======================================================================
    def _poll_queue(self):
        try:
            for _ in range(200):                         # batasi kerja per putaran
                try:
                    event = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception:
                    log.exception("Gagal menangani event %s", event.kind)
                    self._log("Kesalahan internal saat menampilkan event (lihat floodsentinel.log)", "error")
        finally:
            self.root.after(config.GUI_POLL_MS, self._poll_queue)

    def _handle_event(self, event):
        kind, data = event.kind, event.data
        if kind == "telemetry":
            self._on_telemetry(data)
        elif kind == "conn":
            self._on_conn(*data)
        elif kind == "status":
            self._on_status_topic(*data)
        elif kind == "online":
            self._on_online(*data)
        elif kind == "log":
            level, text = data
            self._log(text, {"INFO": "info", "WARN": "warn", "ERROR": "error"}.get(level, "info"))

    def _on_conn(self, state, detail):
        self.conn_state = state
        self.conn_detail = detail
        if state == mqtt_client.CONNECTED:
            self._log("MQTT CONNECTED (%s)" % detail, "ok")
        elif state == mqtt_client.RECONNECTING:
            self._log("MQTT RECONNECTING - %s" % (detail or "mencoba menyambung kembali"), "warn")
        else:
            self._log("MQTT DISCONNECTED", "warn")
        self._update_feed_state()
        self._render_all()

    def _on_status_topic(self, base, status):
        if status != self.status_topic:
            self._log("Topic status (%s): %s" % (base.split("/")[-1], status), status)
        self.status_topic = status
        self._render_all()

    def _on_online(self, base, value):
        if value != self.device_online:
            self._log("Perangkat %s (Last Will/online topic)" % value.upper(),
                      "ok" if value == "online" else "warn")
        self.device_online = value
        self._render_all()

    def _on_telemetry(self, tele):
        self.latest = tele
        self.latest_mono = time.monotonic()
        level = tele.water_level_cm
        rate = tele.rise_rate_cm_min

        if tele.rapid_rise is not None:
            rapid = tele.rapid_rise
        else:
            rapid = rate is not None and rate >= config.RAPID_RISE_THRESHOLD_CM_MIN

        self.history.append((mdates.date2num(tele.received_at),
                             float("nan") if level is None else level, bool(rapid)))
        self.graph_dirty = True

        # ---- event log
        level_text = "n/a" if level is None else "%.1f cm" % level
        rate_text = "n/a" if rate is None else "%+.2f cm/min" % rate
        now_m = time.monotonic()
        if tele.status != self._prev_status:
            if self._prev_status is None:
                self._log("Status awal: %s | Water Level: %s" % (tele.status, level_text), tele.status)
            else:
                self._log("STATUS BERUBAH: %s -> %s | Water Level: %s | Rate: %s"
                          % (self._prev_status, tele.status, level_text, rate_text), tele.status)
            self._last_sample_log = now_m
        elif now_m - self._last_sample_log >= config.LOG_SAMPLE_EVERY_S:
            self._log("Water Level: %s | %s | Rate: %s" % (level_text, tele.status, rate_text), tele.status)
            self._last_sample_log = now_m
        if rapid and not self._prev_rapid:
            self._log("KENAIKAN CEPAT terdeteksi (%s)" % rate_text, "warn")
        elif self._prev_rapid and not rapid:
            self._log("Kenaikan cepat selesai", "info")
        self._prev_status, self._prev_rapid = tele.status, bool(rapid)

        for warning in tele.warnings:                    # catat tiap jenis peringatan sekali saja
            if warning not in self._warn_seen:
                if len(self._warn_seen) > 100:
                    self._warn_seen.clear()
                self._warn_seen.add(warning)
                self._log("Kualitas data: %s" % warning, "warn")

        self._update_feed_state()
        self._render_all()

    # ======================================================================
    # Status aliran data: LIVE / STALE / NO DATA
    # ======================================================================
    def _update_feed_state(self):
        if self.latest is None:
            new = "NO DATA"
        else:
            age = time.monotonic() - self.latest_mono
            live = age <= config.STALE_AFTER_S and self.conn_state == mqtt_client.CONNECTED
            new = "LIVE" if live else "STALE"
        if new == self.feed_state:
            return
        old, self.feed_state = self.feed_state, new
        if new == "STALE":
            age = _fmt_age(time.monotonic() - self.latest_mono)
            self._log("Data TIDAK diperbarui (%s). Telemetry terakhir %s. Broker: %s"
                      % (age, self.latest.received_at.strftime("%H:%M:%S"), self.conn_state), "warn")
        elif new == "LIVE" and old == "STALE":
            self._log("Telemetry diterima kembali. Data LIVE.", "ok")
        self.graph_dirty = True

    def _tick(self):
        try:
            self.clock.configure(text=now_local().strftime("%A, %d %b %Y  %H:%M:%S"))
            self._update_feed_state()
            self._render_connection()
            self._render_banner()
        except Exception:
            log.exception("Kesalahan pada tick GUI")
        self.root.after(config.TICK_MS, self._tick)

    # ======================================================================
    # Render
    # ======================================================================
    def _render_all(self):
        self._render_connection()
        self._render_banner()
        self._render_water()
        self._render_status()
        self._render_rate()

    def _render_banner(self):
        if self.feed_state == "LIVE":
            self.banner.grid_remove()
            return
        self.banner.grid()
        if self.feed_state == "NO DATA":
            self.banner.configure(bg=GRAY, fg="#ffffff",
                                  text="  Menunggu telemetry dari ESP32...  (broker: %s)" % self.conn_state)
        else:
            age = _fmt_age(time.monotonic() - self.latest_mono)
            self.banner.configure(
                bg=ORANGE, fg="#1c1917",
                text="  [!] DATA TIDAK DIPERBARUI sejak %s (%s lalu)  |  broker: %s  |  "
                     "Nilai di bawah adalah DATA TERAKHIR, bukan kondisi saat ini."
                     % (self.latest.received_at.strftime("%H:%M:%S"), age, self.conn_state))

    def _render_connection(self):
        color = CONN_COLORS.get(self.conn_state, GRAY)
        self.conn_dot.itemconfigure(self.conn_dot_id, fill=color)
        self.v_conn.configure(text=self.conn_state, fg=color)
        feed_text = self.feed_state
        if self.feed_state == "STALE" and self.latest_mono is not None:
            feed_text = "STALE (%s)" % _fmt_age(time.monotonic() - self.latest_mono)
        self.v_feed.configure(text=feed_text, fg=FEED_COLORS.get(self.feed_state, GRAY))

        t = self.latest
        self.v_device.configure(text=(t.device_id or "tidak dikirim") if t else "-")
        lwt = {"online": ("ONLINE", GREEN), "offline": ("OFFLINE", RED)}.get(
            self.device_online, ("tidak ada info", MUTED))
        self.v_lwt.configure(text=lwt[0], fg=lwt[1])
        self.v_topic.configure(text=t.base if t else "-")
        if t:
            self.v_last.configure(text="%s (%s lalu)" % (
                t.received_at.strftime("%H:%M:%S"), _fmt_age(time.monotonic() - self.latest_mono)))
        else:
            self.v_last.configure(text="-")
        s = self.processor.stats()
        self.v_total.configure(text="%d  (valid %d | invalid %d | duplikat %d)"
                               % (s["total"], s["telemetry"], s["invalid"], s["duplicate"]))

    def _render_water(self):
        t = self.latest
        dim = self.feed_state != "LIVE"
        level = t.water_level_cm if t else None
        if level is None:
            self.v_level.configure(text="-- cm", fg=MUTED)
        else:
            self.v_level.configure(text="%.1f cm" % level, fg=MUTED if dim else BLUE)
        dist = t.distance_cm if t else None
        self.v_dist.configure(text="-- cm" if dist is None else "%.1f cm" % dist)

        if level is None:
            zone, zcolor = "tidak diketahui", MUTED
        elif level < config.ALERT_THRESHOLD_CM:
            zone, zcolor = "NORMAL", GREEN
        elif level <= config.DANGER_THRESHOLD_CM:
            zone, zcolor = "SIAGA", YELLOW
        else:
            zone, zcolor = "BAHAYA", RED
        self.v_zone.configure(text=zone, fg=MUTED if dim and level is not None else zcolor)
        self._draw_tank(level, dim)

    def _draw_tank(self, level, dim):
        c = self.tank
        c.delete("all")
        x0, x1, top, bottom = 38, 88, 12, 238
        height = bottom - top

        def y_of(v):
            return bottom - height * max(0.0, min(v, config.MAX_LEVEL_CM)) / config.MAX_LEVEL_CM

        c.create_rectangle(x0, y_of(config.ALERT_THRESHOLD_CM), x1, bottom, fill="#14532d", outline="")
        c.create_rectangle(x0, y_of(config.DANGER_THRESHOLD_CM), x1, y_of(config.ALERT_THRESHOLD_CM),
                           fill="#713f12", outline="")
        c.create_rectangle(x0, top, x1, y_of(config.DANGER_THRESHOLD_CM), fill="#7f1d1d", outline="")
        if level is not None:
            c.create_rectangle(x0 + 3, y_of(level), x1 - 3, bottom, outline="",
                               fill=GRAY if dim else BLUE)
            c.create_text(x1 + 6, y_of(level), text="%.0f" % level, anchor="w",
                          fill=MUTED if dim else TEXT, font=(FONT, 9, "bold"))
        c.create_rectangle(x0, top, x1, bottom, outline=BORDER, width=2)
        for value in (0, config.ALERT_THRESHOLD_CM, config.DANGER_THRESHOLD_CM, config.MAX_LEVEL_CM):
            c.create_line(x0 - 4, y_of(value), x0, y_of(value), fill=MUTED)
            c.create_text(x0 - 7, y_of(value), text="%.0f" % value, anchor="e", fill=MUTED,
                          font=(FONT, 8))
        if level is None:
            c.create_text((x0 + x1) / 2, (top + bottom) / 2, text="no\ndata", fill=MUTED,
                          font=(FONT, 10, "bold"), justify="center")

    def _render_status(self):
        t = self.latest
        dim = self.feed_state != "LIVE"
        if t is None:
            key = "WAITING"
            shown = "MENUNGGU DATA"
        else:
            key = t.status if t.status in STATUS_STYLE else "UNKNOWN"
            shown = t.status
        color, fg, desc = STATUS_STYLE[key]
        if dim and t is not None:
            color, fg = _blend(color, "#334155", 0.6), "#cbd5e1"
        self.status_box.configure(text=shown, bg=color, fg=fg)
        self.v_status_desc.configure(text=desc)

        if t is None:
            meta = "Status topic (retained): %s" % (self.status_topic or "belum ada")
        else:
            sensor = t.sensor_status or "tidak dikirim"
            meta = "Base status (ketinggian): %s   |   Sensor: %s" % (t.base_status or "n/a", sensor)
            if dim:
                meta += "\nDATA LAMA: status di atas adalah yang TERAKHIR diterima."
        self.v_status_meta.configure(text=meta, fg=ORANGE if (dim and t) else MUTED)

    def _render_rate(self):
        t = self.latest
        dim = self.feed_state != "LIVE"
        rate = t.rise_rate_cm_min if t else None
        if rate is None:
            self.v_rate.configure(text="-- cm/min", fg=MUTED)
            if t is None:
                note = "Belum ada data."
            elif t.status == "SENSOR_ERROR":
                note = "Tidak tersedia: sensor error."
            else:
                note = "Belum tersedia (warm-up ESP32 ~16 detik) atau tidak dikirim."
            self.v_rapid.configure(text="-", fg=MUTED)
        else:
            rapid = rate >= config.RAPID_RISE_THRESHOLD_CM_MIN
            self.v_rate.configure(text="%+.2f cm/min" % rate,
                                  fg=MUTED if dim else (RED if rapid else TEXT))
            note = "Positif = air naik, negatif = air turun."
            if rapid:
                self.v_rapid.configure(text="KENAIKAN CEPAT (>= %.0f cm/min)" % config.RAPID_RISE_THRESHOLD_CM_MIN,
                                       fg=MUTED if dim else RED)
            else:
                self.v_rapid.configure(text="Tidak ada kenaikan cepat", fg=MUTED)
        if dim and t is not None:
            note += "  (data lama)"
        self.v_rate_note.configure(text=note)

    # ======================================================================
    # Event log
    # ======================================================================
    def _log(self, text, tag="info"):
        stamp = now_local().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[%s] %s\n" % (stamp, text), tag)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > config.MAX_LOG_LINES:                 # batasi memori
            self.log_text.delete("1.0", "%d.0" % (lines - config.MAX_LOG_LINES + 1))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        logging.getLogger("floodsentinel.event").info(text)
