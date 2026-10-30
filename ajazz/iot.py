"""Cliente gRPC-web mínimo para o iot_driver.exe da AJAZZ (127.0.0.1:3814).

O iot_driver é o helper nativo que acompanha o "AJAZZ Driver". Ele expõe o
serviço driver.DriverGrpc; aqui usamos só watchDevList, sendRawFeature e
readRawFeature. O próprio driver calcula o checksum pedido em checksumType.
"""
import http.client
import os
import socket
import struct
import subprocess
import threading
import time

HOST, PORT = "127.0.0.1", 3814

# proto.driver.CheckSumType
BIT7, BIT8, NONE = 0, 1, 2


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _bytes_field(num, data):
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _varint_field(num, value):
    return _varint(num << 3) + _varint(value)


def decode(buf):
    """Decodifica uma mensagem protobuf em [(campo, valor)]; valores LEN ficam como bytes."""
    out, i = [], 0

    def rd():
        nonlocal i
        r = s = 0
        while True:
            c = buf[i]
            i += 1
            r |= (c & 0x7F) << s
            s += 7
            if not c & 0x80:
                return r

    while i < len(buf):
        key = rd()
        field, wire = key >> 3, key & 7
        if wire == 0:
            out.append((field, rd()))
        elif wire == 2:
            n = rd()
            out.append((field, bytes(buf[i:i + n])))
            i += n
        elif wire == 5:
            out.append((field, struct.unpack_from("<I", buf, i)[0]))
            i += 4
        elif wire == 1:
            out.append((field, struct.unpack_from("<Q", buf, i)[0]))
            i += 8
        else:
            raise ValueError(f"wire type {wire} não suportado")
    return out


def _frame(body):
    return b"\x00" + struct.pack(">I", len(body)) + body


def _first_message(data):
    """Primeiro frame de dados de uma resposta gRPC-web, mais o grpc-status do trailer."""
    i, msg, status = 0, None, None
    while i + 5 <= len(data):
        flag, n = data[i], struct.unpack_from(">I", data, i + 1)[0]
        payload = data[i + 5:i + 5 + n]
        i += 5 + n
        if flag & 0x80:
            for line in payload.decode("latin1").split("\r\n"):
                if line.lower().startswith("grpc-status:"):
                    status = int(line.split(":", 1)[1])
        elif msg is None:
            msg = payload
    return msg, status


class DriverError(RuntimeError):
    pass


class IotDriver:
    HEADERS = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1"}

    def __init__(self):
        self._conn = None
        self._lock = threading.Lock()

    # --- processo -------------------------------------------------------
    @staticmethod
    def is_listening():
        try:
            with socket.create_connection((HOST, PORT), timeout=0.3):
                return True
        except OSError:
            return False

    @classmethod
    def ensure_running(cls, exe_path):
        """Inicia o iot_driver.exe se nada estiver escutando na porta 3814."""
        if cls.is_listening():
            return False
        if not exe_path or not os.path.isfile(exe_path):
            raise DriverError(f"iot_driver.exe não encontrado: {exe_path!r}")
        subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(50):
            if cls.is_listening():
                return True
            time.sleep(0.1)
        raise DriverError("iot_driver.exe iniciou mas não abriu a porta 3814")

    # --- RPC ------------------------------------------------------------
    def _unary(self, method, body):
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._conn is None:
                        self._conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
                    self._conn.request("POST", "/driver.DriverGrpc/" + method, _frame(body), self.HEADERS)
                    resp = self._conn.getresponse()
                    data = resp.read()
                    break
                except (OSError, http.client.HTTPException):
                    self._conn = None
                    if attempt:
                        raise
        msg, status = _first_message(data)
        if status not in (None, 0):
            raise DriverError(f"{method}: grpc-status {status}")
        return decode(msg or b"")

    def list_devices(self):
        """Primeira mensagem do stream watchDevList, como lista de dicts proto.driver.Device."""
        conn = http.client.HTTPConnection(HOST, PORT, timeout=3)
        try:
            conn.request("POST", "/driver.DriverGrpc/watchDevList", _frame(b""), self.HEADERS)
            resp = conn.getresponse()
            buf = b""
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= 5 and len(buf) >= 5 + struct.unpack_from(">I", buf, 1)[0]:
                    break
        finally:
            conn.close()
        msg, _ = _first_message(buf)
        devices = []
        for field, item in decode(msg or b""):
            if field != 1:
                continue
            for sub_field, dev in decode(item):
                if sub_field != 1:
                    continue
                f = dict(decode(dev))
                devices.append({
                    "devtype": f.get(1, 0), "is24": bool(f.get(2, 0)),
                    "path": f.get(3, b"").decode(), "id": f.get(4, 0),
                    "battery": f.get(5, 0), "online": bool(f.get(6, 0)),
                    "vid": f.get(7, 0), "pid": f.get(8, 0),
                })
        return devices

    def feature(self, path, msg, checksum=BIT7):
        """Envia um feature report pelo driver e devolve a resposta lida em seguida."""
        p = path.encode()
        body = _bytes_field(1, p) + _bytes_field(2, bytes(msg))
        if checksum:
            body += _varint_field(3, checksum)
        res = dict(self._unary("sendRawFeature", body))
        if res.get(1):
            raise DriverError("sendRawFeature: " + res[1].decode(errors="replace").strip())
        res = dict(self._unary("readRawFeature", _bytes_field(1, p)))
        if res.get(1):
            raise DriverError("readRawFeature: " + res[1].decode(errors="replace").strip())
        return res.get(2, b"")
