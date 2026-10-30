"""Grava o travel bruto e mostra o ruído por tecla. Só lê o teclado.

Uso: python tools/capture.py [segundos] [arquivo.csv]
Feche o app principal antes: duas leituras simultâneas se atrapalham.
"""
import csv
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ajazz.iot import IotDriver
from ajazz.keyboard import Keyboard, load_layout

DRIVER_EXE = os.path.join(ROOT, "..", "AJAZZ Driver", "resources", "app", "iot_driver.exe")


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    out = sys.argv[2] if len(sys.argv) > 2 else None
    IotDriver.ensure_running(DRIVER_EXE)
    layout = load_layout()
    kb = Keyboard(IotDriver(), layout)
    if not kb.connect():
        sys.exit("teclado não encontrado")
    keys = sorted(layout["keys"], key=lambda k: k["index"])
    rows = []
    t0 = time.perf_counter()
    while (now := time.perf_counter()) - t0 < seconds:
        t = kb.read_travel()
        rows.append((now - t0, [t[k["index"]] for k in keys]))
    print(f"{len(rows)} amostras em {seconds:.0f} s = {len(rows) / seconds:.0f} leituras/s")
    print(f"{'tecla':<12}{'idx':>4}{'min':>7}{'max':>7}{'média':>7}{'desvio':>8}{'saltos>0.3':>11}{'trocas':>8}")
    for c, k in enumerate(keys):
        v = [r[1][c] for r in rows]
        if max(v) == 0:
            continue
        diffs = [abs(b - a) for a, b in zip(v, v[1:])]
        changes = sum(d > 0 for d in diffs)
        jumps = sum(d > 0.3 for d in diffs)
        print(f"{k['name'].replace(chr(10), ' '):<12}{k['index']:>4}{min(v):>7.2f}{max(v):>7.2f}"
              f"{statistics.mean(v):>7.2f}{statistics.pstdev(v):>8.3f}{jumps:>11}{changes:>8}")
    if out:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["t"] + [k["name"].replace("\n", " ") for k in keys])
            for t, vals in rows:
                w.writerow([round(t, 4)] + vals)
        print("gravado em", out)


if __name__ == "__main__":
    main()
