"""AJAZZ AK680MAX: layout e leitura do travel de cada tecla (somente leitura)."""
import json
import os
import struct

from .iot import BIT7

LAYOUT_FILE = os.path.join(os.path.dirname(__file__), "ak680max_layout.json")

# Comando GET_MAGNETISM_BY_ARR (0xE5) com o sub-comando "valor de travel da tecla" (0xFE).
# Cada página devolve 32 valores uint16 LE, em centésimos de mm, na ordem da matriz.
CMD_GET_MAGNETISM_BY_ARR = 0xE5
SUB_TRAVEL_VALUE = 0xFE
KEYS_PER_PAGE = 32
MATRIX_SIZE = 128


def load_layout():
    with open(LAYOUT_FILE, encoding="utf-8") as fh:
        return json.load(fh)


class Keyboard:
    def __init__(self, driver, layout):
        self.driver = driver
        self.layout = layout
        self.path = None
        self.device_id = None
        self.echo_frames = 0
        self._values = [0.0] * MATRIX_SIZE
        indices = [k["index"] for k in layout["keys"]]
        self.pages = sorted({i // KEYS_PER_PAGE for i in indices})

    def connect(self):
        """Procura a interface vendor (devtype 0) do modelo no watchDevList."""
        for dev in self.driver.list_devices():
            if (dev["vid"], dev["pid"]) == (self.layout["vid"], self.layout["pid"]) and dev["devtype"] == 0 and dev["path"]:
                self.path, self.device_id = dev["path"], dev["id"]
                return dev
        self.path = None
        return None

    def read_travel(self):
        """Lista de MATRIX_SIZE valores em mm (0.0 = solta); páginas não usadas ficam em 0."""
        for page in self.pages:
            msg = bytes([CMD_GET_MAGNETISM_BY_ARR, SUB_TRAVEL_VALUE, 1, page, 0, 0, 0, 0])
            data = self.driver.feature(self.path, msg, BIT7)
            if len(data) < 2 * KEYS_PER_PAGE:
                raise IOError(f"resposta curta na página {page}: {len(data)} bytes")
            # Às vezes o firmware ainda não respondeu e a leitura devolve o próprio pedido.
            # Esse frame não traz dados: a página mantém os valores da leitura anterior.
            if data[:2] == msg[:2]:
                self.echo_frames += 1
                continue
            raw = struct.unpack_from(f"<{KEYS_PER_PAGE}H", data, 0)
            base = page * KEYS_PER_PAGE
            for i, v in enumerate(raw):
                self._values[base + i] = v / 100.0
        return list(self._values)
