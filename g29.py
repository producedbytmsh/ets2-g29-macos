#!/usr/bin/env python3
"""
g29.py - Logitech G29 helper for macOS (Apple Silicon), aimed at Euro Truck
Simulator 2 running through CrossOver / Wine.

macOS has NO Logitech driver for the G-series wheels (G HUB only supports mice/
keyboards/headsets), and G HUB won't install under Wine. So the wheel boots in a
restricted range with no software control. This tool talks to the wheel directly
over USB HID - the same commands the Linux new-lg4ff / Oversteer projects use - to:

  * set the rotation range (default 900 deg),
  * enable/disable/scale the auto-centering spring,
  * and, in `auto` mode, make the spring SPEED-SENSITIVE by reading the truck's
    live speed from the SCS telemetry plugin (loose when parked, firmer the
    faster you drive). ETS2's own force feedback does not pass through CrossOver,
    so this fakes the most important part of it.

Commands:
    python3 g29.py range [degrees]        # set rotation range (default 900)
    python3 g29.py center <0-100>         # set a fixed centering spring %
    python3 g29.py off                    # disable the centering spring
    python3 g29.py auto [options]         # speed-sensitive centering daemon
    python3 g29.py find-speed             # calibrate: find the speed byte offset

`auto` options:
    --max-strength 0.60   spring % at/above --max-kmh (0..1)
    --max-kmh 90          speed at which the spring reaches max
    --curve 0.5           ramp exponent; <1 = more bite at low speed
    --offset 948          speed byte offset in the telemetry block (see find-speed)

Nothing is persistent: the wheel resets on unplug/power-cycle, so re-run after
replugging. Ctrl-C stops `auto` and releases the spring.

Requires:  brew install hidapi
           + the SCS telemetry plugin (RenCloud scs-telemetry.dll) in
             ".../Euro Truck Simulator 2/bin/win_x64/plugins/"  (auto mode only)
See README.md.
"""
import os, sys, glob, struct, time, argparse, ctypes
from ctypes import (c_ushort, c_wchar_p, c_char_p, c_void_p, c_int,
                    c_ubyte, c_size_t, POINTER, Structure)

# --- Logitech G29. Other wheels (G920/G923) use a different FFB protocol. ---
VID, PID = 0x046D, 0xC24F
WHEEL_USAGE_PAGE, WHEEL_USAGE = 0x0001, 0x0004   # Generic Desktop / Joystick

# --- SCS telemetry shared-memory layout (RenCloud scs-telemetry.dll) ---
SDK_OFF, GAME_OFF = 0, 52          # sdkActive (bool), game (1=ETS2, 2=ATS)
DEFAULT_SPEED_OFF = 948            # truck speed float (m/s) - verify with find-speed!
STATUS_FILE = "/tmp/g29_status.txt"


# ---------------------------------------------------------------- libhidapi
def load_hidapi():
    for cand in ("/opt/homebrew/lib/libhidapi.dylib",   # Apple Silicon Homebrew
                 "/usr/local/lib/libhidapi.dylib",       # Intel Homebrew
                 ctypes.util.find_library("hidapi") if hasattr(ctypes, "util") else None):
        if cand and os.path.exists(cand):
            return ctypes.CDLL(cand)
    import ctypes.util as cu
    p = cu.find_library("hidapi")
    if p:
        return ctypes.CDLL(p)
    sys.exit("libhidapi not found. Install it with:  brew install hidapi")

import ctypes.util  # noqa: E402
lib = load_hidapi()

class DevInfo(Structure):
    pass
DevInfo._fields_ = [
    ("path", c_char_p), ("vendor_id", c_ushort), ("product_id", c_ushort),
    ("serial_number", c_wchar_p), ("release_number", c_ushort),
    ("manufacturer_string", c_wchar_p), ("product_string", c_wchar_p),
    ("usage_page", c_ushort), ("usage", c_ushort), ("interface_number", c_int),
    ("next", POINTER(DevInfo)), ("bus_type", c_int),
]
lib.hid_enumerate.argtypes = [c_ushort, c_ushort]; lib.hid_enumerate.restype = POINTER(DevInfo)
lib.hid_free_enumeration.argtypes = [POINTER(DevInfo)]; lib.hid_free_enumeration.restype = None
lib.hid_open_path.argtypes = [c_char_p]; lib.hid_open_path.restype = c_void_p
lib.hid_close.argtypes = [c_void_p]; lib.hid_close.restype = None
lib.hid_write.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]; lib.hid_write.restype = c_int
lib.hid_init()
try:  # keep the game's access to the wheel (hidapi >= 0.12 default, set explicitly)
    lib.hid_darwin_set_open_exclusive.argtypes = [c_int]
    lib.hid_darwin_set_open_exclusive(0)
except Exception:
    pass


def find_wheel_path():
    head = lib.hid_enumerate(VID, PID)
    p, path = head, None
    while p:
        d = p.contents
        if d.usage_page == WHEEL_USAGE_PAGE and d.usage == WHEEL_USAGE:
            path = bytes(d.path) if d.path else None
            break
        p = d.next
    if head:
        lib.hid_free_enumeration(head)
    return path


def send_reports(path, reports):
    """Open -> write -> close, instantly. NEVER holds the device open, or the
    game loses access to the wheel and pedals."""
    dev = lib.hid_open_path(path)
    if not dev:
        return False
    ok = True
    for r in reports:
        rep = [0x00] + r                      # 0x00 = report-id prefix for hid_write
        buf = (c_ubyte * len(rep))(*rep)
        if lib.hid_write(dev, buf, len(rep)) < 0:
            ok = False
    lib.hid_close(dev)
    return ok


# --------------------------------------------------- HID command builders (lg4ff)
def range_report(deg):
    deg = max(40, min(900, int(deg)))
    return [0xf8, 0x81, deg & 0xff, (deg >> 8) & 0xff, 0, 0, 0]

def autocenter_reports(mag):
    """mag: 0..0xffff. Replicates new-lg4ff lg4ff_set_autocenter_default for
    non-MOMO wheels (G25/G27/G29/DFGT)."""
    if mag <= 0:
        return [[0xf5, 0, 0, 0, 0, 0, 0]]     # disable default spring
    mag = min(mag, 0xffff)
    if mag <= 0xaaaa:
        ea, eb = 0x0c * mag, 0x80 * mag
    else:
        ea = 0x0c * 0xaaaa + 0x06 * (mag - 0xaaaa)
        eb = 0x80 * 0xaaaa + 0xff * (mag - 0xaaaa)
    ea >>= 1
    a = (ea // 0xaaaa) & 0xff
    b = (eb // 0xaaaa) & 0xff
    return [[0xfe, 0x0d, a, a, b, 0, 0], [0x14, 0, 0, 0, 0, 0, 0]]


# ------------------------------------------------------------- telemetry (Wine)
def wine_globs():
    uid = os.getuid()
    globs = [f"/tmp/.wine-{uid}/server-*/tmpmap-*"]
    tmp = os.environ.get("TMPDIR", "")
    if tmp:
        globs.append(os.path.join(tmp, f".wine-{uid}", "server-*", "tmpmap-*"))
    env = os.environ.get("G29_WINE_GLOB")
    if env:
        globs.insert(0, env)
    return globs

def candidates():
    files = []
    for g in wine_globs():
        files.extend(glob.glob(g))
    return files

def read_head(path, n=1024):
    with open(path, "rb") as fh:
        return fh.read(n)

def is_ets2_block(data, speed_off):
    if len(data) < speed_off + 4:
        return False
    try:
        active = data[SDK_OFF]
        game = struct.unpack_from("<I", data, GAME_OFF)[0]
        speed = struct.unpack_from("<f", data, speed_off)[0]
    except Exception:
        return False
    return active == 1 and game == 1 and -60.0 < speed < 200.0

def find_map(speed_off):
    for f in candidates():
        try:
            if is_ets2_block(read_head(f), speed_off):
                return f
        except Exception:
            pass
    return None

def read_speed(path, speed_off):
    with open(path, "rb") as fh:
        fh.seek(speed_off)
        b = fh.read(4)
    return struct.unpack("<f", b)[0] if len(b) == 4 else None

def status(msg):
    try:
        with open(STATUS_FILE, "w") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------- commands
def cmd_range(args):
    path = find_wheel_path()
    if not path:
        sys.exit("G29 not found (VID 046d / PID c24f). Plugged in and powered?")
    ok = send_reports(path, [range_report(args.degrees)])
    print(f"{'OK' if ok else 'FAILED'} - rotation range set to {int(args.degrees)} deg")

def cmd_center(args):
    path = find_wheel_path()
    if not path:
        sys.exit("G29 not found.")
    mag = int(max(0.0, min(1.0, args.percent / 100.0)) * 0xffff)
    ok = send_reports(path, autocenter_reports(mag))
    print(f"{'OK' if ok else 'FAILED'} - centering spring set to {args.percent:.0f}%")

def cmd_off(args):
    path = find_wheel_path()
    if not path:
        sys.exit("G29 not found.")
    ok = send_reports(path, [[0xf5, 0, 0, 0, 0, 0, 0]])
    print(f"{'OK' if ok else 'FAILED'} - centering spring disabled")

def cmd_auto(args):
    path = find_wheel_path()
    if not path:
        sys.exit("G29 not found (VID 046d / PID c24f). Plugged in and powered?")
    send_reports(path, [range_report(args.range)])
    print(f"auto-center running: 0% parked -> {int(args.max_strength*100)}% at "
          f"{args.max_kmh:.0f} km/h (curve {args.curve}, offset {args.offset}). Ctrl-C to stop.")
    status("starting; waiting for telemetry")
    tmap, last_mag, last_send, last_log = None, -1, 0.0, 0.0
    try:
        while True:
            now = time.time()
            if tmap is None or not os.path.exists(tmap):
                tmap = find_map(args.offset)
            spd = None
            if tmap:
                try:
                    spd = read_speed(tmap, args.offset)
                except Exception:
                    tmap = None
            if spd is not None:
                kmh = abs(spd) * 3.6
                if kmh < 2.0:
                    frac = 0.0
                else:
                    frac = (min(kmh, args.max_kmh) / args.max_kmh) ** args.curve * args.max_strength
                mag = int(frac * 0xffff)
                if (last_mag < 0 or abs(mag - last_mag) > 0x0300) and now - last_send > 0.2:
                    if send_reports(path, autocenter_reports(mag)):
                        last_mag, last_send = mag, now
                    else:
                        path = find_wheel_path() or path
                if now - last_log > 1.0:
                    status(f"{kmh:5.1f} km/h  ->  spring {int(frac*100):3d}%")
                    last_log = now
            elif now - last_log > 2.0:
                status("no telemetry (in menu / game paused / plugin not enabled?)")
                last_log = now
            time.sleep(0.1)
    except KeyboardInterrupt:
        send_reports(path, [[0xf5, 0, 0, 0, 0, 0, 0]])
        status("stopped; spring released")
        print("\nStopped, spring released.")

def cmd_find_speed(args):
    """Record the whole telemetry block while you drive a stop->accelerate->stop
    profile, then report which byte offset traces your speed."""
    print("Calibration. Keep ETS2 FOCUSED for the next %ds and drive:" % args.seconds)
    print("  1) full stop (~5s)  2) accelerate to ~60 km/h  3) hold  4) brake to a stop")
    print("Recording...")
    snaps, t0 = [], time.time()
    while time.time() - t0 < args.seconds:
        f = None
        for c in candidates():
            try:
                d = read_head(c, 8192)
                if len(d) >= 60 and d[0] == 1 and struct.unpack_from("<I", d, GAME_OFF)[0] == 1:
                    f = (c, d); break
            except Exception:
                pass
        if f:
            snaps.append(f[1])
        time.sleep(0.3)
    if len(snaps) < 5:
        sys.exit("Too few samples - is the plugin enabled and are you in a drive?")
    best = []
    for off in range(0, 8192 - 4):
        series, ok = [], True
        for d in snaps:
            v = struct.unpack_from("<f", d, off)[0]
            if v != v or abs(v) > 1e4:
                ok = False; break
            series.append(v)
        if not ok:
            continue
        lo, hi = min(series), max(series)
        if lo < 1.5 and 6.0 < hi < 30.0 and (hi - lo) > 4.0:   # 0 -> peak -> 0, in m/s
            best.append((hi - lo, off, lo, hi))
    best.sort(reverse=True)
    if not best:
        sys.exit("No speed-shaped offset found. Drive a clearer stop->go->stop and retry.")
    print("\nLikely speed offsets (min~0, peaks 20-110 km/h, biggest swing first):")
    for rng, off, lo, hi in best[:6]:
        print(f"  --offset {off:4d}   ({lo*3.6:.0f}..{hi*3.6:.0f} km/h)")
    print(f"\nUse the top one:  python3 g29.py auto --offset {best[0][1]}")


def main():
    ap = argparse.ArgumentParser(description="Logitech G29 helper for macOS + ETS2/CrossOver")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("range", help="set rotation range in degrees (default 900)")
    p.add_argument("degrees", nargs="?", type=float, default=900.0)
    p.set_defaults(func=cmd_range)

    p = sub.add_parser("center", help="set a fixed centering spring percent")
    p.add_argument("percent", type=float)
    p.set_defaults(func=cmd_center)

    p = sub.add_parser("off", help="disable the centering spring")
    p.set_defaults(func=cmd_off)

    p = sub.add_parser("auto", help="speed-sensitive centering daemon")
    p.add_argument("--max-strength", type=float, default=0.60)
    p.add_argument("--max-kmh", type=float, default=90.0)
    p.add_argument("--curve", type=float, default=0.5)
    p.add_argument("--range", type=float, default=900.0)
    p.add_argument("--offset", type=int, default=DEFAULT_SPEED_OFF)
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("find-speed", help="calibrate the telemetry speed offset")
    p.add_argument("--seconds", type=int, default=45)
    p.set_defaults(func=cmd_find_speed)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
