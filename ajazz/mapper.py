"""Converte o travel das teclas (mm) em um relatório XUSB (Xbox 360)."""
import math

from .vigem import BUTTONS, XUSB_REPORT

TRIGGERS = ("LT", "RT")
AXES = ("LX+", "LX-", "LY+", "LY-", "RX+", "RX-", "RY+", "RY-")
TARGETS = TRIGGERS + AXES + tuple(BUTTONS)

DEFAULTS = {"start": 0.2, "end": 3.0, "curve": 1.0, "threshold": 1.2, "hysteresis": 0.1, "filter": 0.12}
FULL_TRAVEL = 3.5  # o firmware satura o travel em 3,50 mm


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


class Mapper:
    def __init__(self, profile):
        self.set_profile(profile)

    def set_profile(self, profile):
        self.profile = profile
        self._pressed = {}
        self._held = {}

    def smooth(self, travel):
        """Filtro de folga: a saída só anda quando a tecla se afasta mais que a banda dela.

        O firmware salta cerca de 0,10 mm sempre que a tecla inverte a direção, então um
        microtremor do dedo vira degraus no stick. Com a banda maior que esse salto, a saída
        fica parada durante o tremor e acompanha sem degrau quando o movimento é real.
        """
        band = self.profile.get("filter", DEFAULTS["filter"])
        out = []
        for i, t in enumerate(travel):
            h = self._held.get(i, 0.0)
            if t <= 0.0 or t >= FULL_TRAVEL:
                h = t  # solta ou no fundo: sem atraso nos extremos
            elif t > h + band:
                h = t - band
            elif t < h - band:
                h = t + band
            self._held[i] = h
            out.append(h)
        return out

    @staticmethod
    def analog(travel, b):
        start = b.get("start", DEFAULTS["start"])
        end = max(b.get("end", DEFAULTS["end"]), start + 0.05)
        v = _clamp((travel - start) / (end - start))
        return v ** b.get("curve", DEFAULTS["curve"])

    def _button(self, i, travel, b):
        thr = b.get("threshold", DEFAULTS["threshold"])
        hys = b.get("hysteresis", DEFAULTS["hysteresis"])
        on = travel >= thr if not self._pressed.get(i) else travel > thr - hys
        self._pressed[i] = on
        return 1.0 if on else 0.0

    def compute(self, travel):
        """Retorna (XUSB_REPORT, valor normalizado 0..1 de cada binding)."""
        axis = dict.fromkeys(AXES, 0.0)
        trig = dict.fromkeys(TRIGGERS, 0.0)
        buttons = 0
        values = []
        for i, b in enumerate(self.profile.get("bindings", [])):
            idx, target = b.get("key"), b.get("target")
            t = travel[idx] if isinstance(idx, int) and 0 <= idx < len(travel) else 0.0
            if target in BUTTONS:
                v = self._button(i, t, b)
                if v:
                    buttons |= BUTTONS[target]
            elif target in TRIGGERS:
                v = self.analog(t, b)
                trig[target] = max(trig[target], v)
            elif target in AXES:
                v = self.analog(t, b)
                axis[target] = max(axis[target], v)
            else:
                v = 0.0
            values.append(round(v, 4))

        sticks = {}
        for s in ("L", "R"):
            x = axis[s + "X+"] - axis[s + "X-"]
            y = axis[s + "Y+"] - axis[s + "Y-"]
            mag = math.hypot(x, y)
            if self.profile.get("circular", True) and mag > 1.0:
                x, y = x / mag, y / mag
            sticks[s] = (_clamp(x, -1, 1), _clamp(y, -1, 1))

        to_short = lambda v: int(round(v * 32767))
        report = XUSB_REPORT(
            wButtons=buttons,
            bLeftTrigger=int(round(trig["LT"] * 255)), bRightTrigger=int(round(trig["RT"] * 255)),
            sThumbLX=to_short(sticks["L"][0]), sThumbLY=to_short(sticks["L"][1]),
            sThumbRX=to_short(sticks["R"][0]), sThumbRY=to_short(sticks["R"][1]),
        )
        return report, values


def report_to_dict(r):
    return {"buttons": [n for n, m in BUTTONS.items() if r.wButtons & m],
            "LT": r.bLeftTrigger, "RT": r.bRightTrigger,
            "LX": r.sThumbLX, "LY": r.sThumbLY, "RX": r.sThumbRX, "RY": r.sThumbRY}
