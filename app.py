"""AJAZZ Gamepad: transforma o travel analógico do AK680MAX em um controle Xbox 360 virtual.

Somente LÊ o teclado (comando GET 0xE5); nada é gravado no firmware.
Uso: python app.py [--port 8765] [--driver CAMINHO\\iot_driver.exe] [--no-browser]
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ajazz.iot import DriverError, IotDriver
from ajazz.keyboard import Keyboard, load_layout
from ajazz.mapper import DEFAULTS, TARGETS, Mapper, report_to_dict
from ajazz.vigem import XUSB_REPORT, VigemError, X360Pad

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
PROFILE_FILE = os.path.join(ROOT, "profiles", "default.json")
DRIVER_CANDIDATES = [
    os.environ.get("AJAZZ_IOT_DRIVER", ""),
    os.path.join(ROOT, "..", "AJAZZ Driver", "resources", "app", "iot_driver.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "AJAZZ Driver", "resources", "app", "iot_driver.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""), "AJAZZ Driver", "resources", "app", "iot_driver.exe"),
]

EMPTY_PROFILE = {"name": "Padrão", "circular": True, "bindings": []}


def load_profile():
    try:
        with open(PROFILE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return dict(EMPTY_PROFILE)


def save_profile(profile):
    os.makedirs(os.path.dirname(PROFILE_FILE), exist_ok=True)
    tmp = PROFILE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILE_FILE)


class Engine(threading.Thread):
    """Laço de leitura do teclado -> mapeamento -> controle virtual."""

    def __init__(self, driver_exe):
        super().__init__(daemon=True)
        self.driver_exe = driver_exe
        self.driver = IotDriver()
        self.layout = load_layout()
        self.keyboard = Keyboard(self.driver, self.layout)
        self.mapper = Mapper(load_profile())
        self.pad = None
        self._pad_retry = 0.0
        self.enabled = True
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.state = {"connected": False, "error": None, "hz": 0, "travel": [], "values": [],
                      "output": report_to_dict(XUSB_REPORT()), "device": None, "pad": False}

    def set_profile(self, profile):
        with self.lock:
            self.mapper.set_profile(profile)
        save_profile(profile)

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _ensure_pad(self):
        """Cria o controle virtual; a criação pode falhar de forma passageira, então tenta de novo."""
        if self.pad or time.monotonic() < self._pad_retry:
            return
        try:
            self.pad = X360Pad()
            self._set(pad=True, error=None)
        except Exception as e:  # ViGEm ausente: ainda dá para ver o travel na UI
            self._pad_retry = time.monotonic() + 2.0
            self._set(pad=False, error=f"Controle virtual indisponível: {e}")

    def _send(self, report):
        if not self.pad:
            return
        try:
            self.pad.update(report)
        except VigemError as e:
            self.pad.close()
            self.pad = None
            self._set(pad=False, error=str(e))

    def run(self):
        frames, t_rate = 0, time.perf_counter()
        while not self.stop_event.is_set():
            self._ensure_pad()
            try:
                if not self.keyboard.path:
                    IotDriver.ensure_running(self.driver_exe)
                    dev = self.keyboard.connect()
                    if not dev:
                        self._set(connected=False, error="Teclado AK680MAX (VID 3151 / PID 502D) não encontrado.")
                        time.sleep(1.0)
                        continue
                    self._set(connected=True, device=dev, error=None if self.pad else self.state["error"])
                travel = self.keyboard.read_travel()
                with self.lock:
                    travel = self.mapper.smooth(travel)
                    report, values = self.mapper.compute(travel)
                if not self.enabled:
                    report = XUSB_REPORT()
                self._send(report)
                frames += 1
                now = time.perf_counter()
                if now - t_rate >= 0.5:
                    self._set(hz=round(frames / (now - t_rate)))
                    frames, t_rate = 0, now
                self._set(travel=[round(v, 2) for v in travel], values=values, output=report_to_dict(report))
                time.sleep(0.001)
            except (DriverError, OSError, IOError) as e:
                self.keyboard.path = None
                self._send(XUSB_REPORT())
                self._set(connected=False, error=str(e))
                time.sleep(1.0)
            except Exception as e:
                traceback.print_exc()
                self._set(error=f"Erro inesperado: {e}")
                time.sleep(1.0)
        self._send(XUSB_REPORT())
        if self.pad:
            self.pad.close()

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            s["enabled"] = self.enabled
            return s


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            elif self.path == "/api/layout":
                self._send(200, engine.layout)
            elif self.path == "/api/meta":
                self._send(200, {"targets": TARGETS, "defaults": DEFAULTS})
            elif self.path == "/api/state":
                self._send(200, engine.snapshot())
            elif self.path == "/api/profile":
                self._send(200, engine.mapper.profile)
            else:
                self._send(404, {"error": "não encontrado"})

        def do_POST(self):
            # Só aceita chamadas da própria página (evita outro site mexer no perfil).
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                return self._send(403, {"error": "origem recusada"})
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "JSON inválido"})
            if self.path == "/api/profile":
                if not isinstance(body.get("bindings"), list):
                    return self._send(400, {"error": "perfil sem bindings"})
                engine.set_profile(body)
                self._send(200, {"ok": True})
            elif self.path == "/api/enabled":
                engine.enabled = bool(body.get("enabled"))
                self._send(200, {"ok": True})
            elif self.path == "/api/quit":
                self._send(200, {"ok": True})
                # shutdown() bloqueia até o serve_forever terminar, então não pode rodar neste thread.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send(404, {"error": "não encontrado"})

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--driver", help="caminho do iot_driver.exe")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    driver_exe = args.driver or next((p for p in DRIVER_CANDIDATES if p and os.path.isfile(p)), None)
    if not driver_exe and not IotDriver.is_listening():
        sys.exit("iot_driver.exe não encontrado. Informe com --driver ou a variável AJAZZ_IOT_DRIVER.")

    engine = Engine(driver_exe)
    engine.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"AJAZZ Gamepad rodando em {url}  (Ctrl+C para sair)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop_event.set()
        engine.join(timeout=3)
        server.server_close()


if __name__ == "__main__":
    main()
