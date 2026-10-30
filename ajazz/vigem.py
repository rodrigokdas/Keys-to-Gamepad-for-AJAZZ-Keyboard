"""Controle Xbox 360 virtual via ViGEmBus, usando o ViGEmClient.dll (ctypes)."""
import ctypes
import os
from ctypes import CDLL, Structure, c_short, c_ubyte, c_uint, c_ushort, c_void_p

DLL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "ViGEmClient.dll")
VIGEM_ERROR_NONE = 0x20000000

BUTTONS = {
    "DPAD_UP": 0x0001, "DPAD_DOWN": 0x0002, "DPAD_LEFT": 0x0004, "DPAD_RIGHT": 0x0008,
    "START": 0x0010, "BACK": 0x0020, "LS": 0x0040, "RS": 0x0080,
    "LB": 0x0100, "RB": 0x0200, "GUIDE": 0x0400,
    "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
}


class XUSB_REPORT(Structure):
    _fields_ = [("wButtons", c_ushort), ("bLeftTrigger", c_ubyte), ("bRightTrigger", c_ubyte),
                ("sThumbLX", c_short), ("sThumbLY", c_short), ("sThumbRX", c_short), ("sThumbRY", c_short)]


class VigemError(RuntimeError):
    pass


class X360Pad:
    def __init__(self):
        dll = CDLL(DLL_PATH)
        for name, args, res in [
            ("vigem_alloc", (), c_void_p), ("vigem_free", (c_void_p,), None),
            ("vigem_connect", (c_void_p,), c_uint), ("vigem_disconnect", (c_void_p,), None),
            ("vigem_target_x360_alloc", (), c_void_p), ("vigem_target_free", (c_void_p,), None),
            ("vigem_target_add", (c_void_p, c_void_p), c_uint),
            ("vigem_target_remove", (c_void_p, c_void_p), c_uint),
            ("vigem_target_x360_update", (c_void_p, c_void_p, XUSB_REPORT), c_uint),
        ]:
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, res
        self._dll = dll
        self._client = dll.vigem_alloc()
        err = dll.vigem_connect(self._client)
        if err != VIGEM_ERROR_NONE:
            dll.vigem_free(self._client)
            raise VigemError(f"vigem_connect falhou (0x{err:08X}); o ViGEmBus está instalado?")
        self._target = dll.vigem_target_x360_alloc()
        err = dll.vigem_target_add(self._client, self._target)
        if err != VIGEM_ERROR_NONE:
            self._release(remove=False)
            raise VigemError(f"vigem_target_add falhou (0x{err:08X})")

    def update(self, report):
        err = self._dll.vigem_target_x360_update(self._client, self._target, report)
        if err != VIGEM_ERROR_NONE:
            raise VigemError(f"vigem_target_x360_update falhou (0x{err:08X})")

    def _release(self, remove=True):
        if remove:
            self._dll.vigem_target_remove(self._client, self._target)
        self._dll.vigem_target_free(self._target)
        self._dll.vigem_disconnect(self._client)
        self._dll.vigem_free(self._client)
        self._client = self._target = None

    def close(self):
        if self._client:
            self._release()
