"""
main.py - Titik masuk FloodSentinel Dashboard (Milestone 5).

Jalankan:  python main.py
"""

import logging
import queue
import sys
import tkinter as tk

import config
from data_handler import MessageProcessor
from mqtt_client import MqttWorker


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
    except OSError:
        pass                                  # folder tidak bisa ditulis: cukup log ke konsol
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def main():
    setup_logging()
    log = logging.getLogger("floodsentinel")
    log.info("FloodSentinel dashboard dimulai")

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("Tidak bisa membuka jendela Tkinter: %s" % exc)
        print("Di Linux tanpa layar: jalankan lewat desktop, atau 'xvfb-run python main.py'.")
        return 1

    from dashboard import Dashboard           # import setelah Tk siap (matplotlib TkAgg)

    event_queue = queue.Queue(maxsize=config.QUEUE_MAX)
    processor = MessageProcessor(event_queue)
    worker = MqttWorker(processor)
    Dashboard(root, event_queue, processor, worker)

    def on_close():
        log.info("Menutup aplikasi")
        try:
            worker.stop()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    worker.start()                            # setelah GUI siap; tidak memblokir
    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
