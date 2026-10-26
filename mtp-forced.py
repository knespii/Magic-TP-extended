#!/usr/bin/env python3
"""mtp-forced: Force Click haptics for the Apple Magic Trackpad 2 on Linux.

The normal click is left to the trackpad firmware and the kernel driver
(hid-magicmouse). This daemon only watches finger pressure on the evdev
device and, once you press harder past a threshold while the button is
already down, plays an extra haptic pulse through hidraw - the same
"second click" feel as Force Click on macOS.

No dependencies beyond the Python 3 standard library.
"""

import argparse
import array
import errno
import fcntl
import os
import select
import struct
import sys
import time

# (vendor, product) pairs. 0x004C is Apple's Bluetooth vendor ID, 0x05AC the USB one.
SUPPORTED_IDS = {
    (0x004C, 0x0265),  # Magic Trackpad 2, Bluetooth
    (0x05AC, 0x0265),  # Magic Trackpad 2, USB (Lightning)
    (0x004C, 0x0324),  # Magic Trackpad (USB-C), Bluetooth
    (0x05AC, 0x0324),  # Magic Trackpad (USB-C), USB
}

# Linux input constants
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_LEFT = 0x110
ABS_MT_SLOT = 0x2F
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


def eviocgabs(code):
    return 0x80184540 + code


def haptic_report(strength, sharp, deep):
    """15-byte output report 0xF2/0x53 that plays one actuator pulse.

    Byte 3 is the overall strength, byte 6 a sharper "click" component
    and byte 11 a deeper "thud" component. The remaining bytes are a
    fixed waveform header taken from the reverse-engineered protocol.
    """
    return bytes([
        0xF2, 0x53, 0x01, strength, 0x78, 0x02, sharp, 0x24,
        0x30, 0x06, 0x01, deep, 0x18, 0x48, 0x12,
    ])


def read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def parse_hid_id(text):
    # HID_ID=0005:0000004C:00000265 -> (0x004C, 0x0265)
    for line in (text or "").splitlines():
        if line.startswith("HID_ID="):
            _, vendor, product = line.split("=", 1)[1].split(":")
            return int(vendor, 16), int(product, 16)
    return None


def find_devices():
    """Return (evdev_path, hidraw_path) for the first supported trackpad.

    Both nodes are matched through their shared HID parent in sysfs, so
    several trackpads or a reconnect never mix up the pair.
    """
    for name in sorted(os.listdir("/sys/class/hidraw")):
        hid_dir = os.path.realpath(f"/sys/class/hidraw/{name}/device")
        ids = parse_hid_id(read_sys(f"{hid_dir}/uevent"))
        if ids not in SUPPORTED_IDS:
            continue
        for ev in sorted(os.listdir("/sys/class/input")):
            if not ev.startswith("event"):
                continue
            ev_dir = os.path.realpath(f"/sys/class/input/{ev}/device")
            if not ev_dir.startswith(hid_dir + "/"):
                continue
            # The trackpad interface is the one with multitouch pressure.
            try:
                fd = os.open(f"/dev/input/{ev}", os.O_RDONLY)
            except OSError:
                continue
            try:
                buf = array.array("i", [0] * 6)
                fcntl.ioctl(fd, eviocgabs(ABS_MT_PRESSURE), buf)
                return f"/dev/input/{ev}", f"/dev/{name}"
            except OSError:
                pass
            finally:
                os.close(fd)
    return None, None


class ForceClick:
    def __init__(self, args, hid_fd):
        self.args = args
        self.hid_fd = hid_fd
        self.down = haptic_report(args.strength, args.sharp, args.deep)
        self.up = haptic_report(args.release_strength, 0, 0)
        self.slot = 0
        self.pressure = {}
        self.button = False
        self.forced = False

    def pulse(self, report):
        os.write(self.hid_fd, report)

    def handle(self, etype, code, value):
        if etype == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif code == ABS_MT_PRESSURE:
                self.pressure[self.slot] = value
            elif code == ABS_MT_TRACKING_ID and value == -1:
                self.pressure.pop(self.slot, None)
        elif etype == EV_KEY and code == BTN_LEFT:
            self.button = bool(value)
        elif etype == EV_SYN and code == SYN_REPORT:
            self.frame()

    def frame(self):
        p = max(self.pressure.values(), default=0)
        a = self.args
        if a.monitor:
            bar = "#" * (p * 40 // 253)
            mark = " FORCE" if self.forced else (" click" if self.button else "")
            print(f"\rpressure {p:3d} {bar:<40}{mark:<6}", end="", flush=True)
        armed = self.button or a.no_button
        if not self.forced and armed and p >= a.threshold:
            self.forced = True
            self.pulse(self.down)
            if a.verbose:
                print(f"force-click pressure={p}", flush=True)
        elif self.forced and (p <= a.threshold - a.hysteresis or not armed):
            self.forced = False
            if a.release_pulse:
                self.pulse(self.up)
            if a.verbose:
                print(f"force-release pressure={p}", flush=True)


def open_pair(args):
    ev, hid = args.evdev, args.hidraw
    if not (ev and hid):
        found_ev, found_hid = find_devices()
        ev, hid = ev or found_ev, hid or found_hid
    if not (ev and hid):
        return None
    return ev, hid, os.open(ev, os.O_RDONLY), os.open(hid, os.O_WRONLY)


def run(args):
    waiting_logged = False
    while True:
        try:
            pair = open_pair(args)
        except PermissionError as e:
            sys.exit(f"Permission denied: {e.filename}. Install udev/70-magic-trackpad.rules (see README).")
        if not pair:
            if not waiting_logged:
                print("Waiting for Magic Trackpad...", flush=True)
                waiting_logged = True
            time.sleep(2)
            continue
        waiting_logged = False
        ev, hid, ev_fd, hid_fd = pair
        print(f"Connected: {ev} + {hid} (threshold {args.threshold})", flush=True)
        fc = ForceClick(args, hid_fd)
        buf = array.array("i", [0] * 6)
        try:
            fcntl.ioctl(ev_fd, eviocgabs(ABS_MT_SLOT), buf)
            fc.slot = buf[0]
        except OSError:
            pass
        try:
            while True:
                select.select([ev_fd], [], [])
                data = os.read(ev_fd, EVENT_SIZE * 64)
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(EVENT_FORMAT, data, off)
                    fc.handle(etype, code, value)
        except OSError as e:
            if e.errno not in (errno.ENODEV, errno.EIO, errno.ENXIO):
                raise
            print("\nTrackpad disconnected.", flush=True)
        finally:
            os.close(ev_fd)
            os.close(hid_fd)
        time.sleep(1)


def test_pulse(args):
    _, hid = (None, args.hidraw) if args.hidraw else find_devices()
    if not hid:
        sys.exit("Magic Trackpad not found.")
    fd = os.open(hid, os.O_WRONLY)
    try:
        for _ in range(3):
            os.write(fd, haptic_report(args.strength, args.sharp, args.deep))
            time.sleep(0.08)
            os.write(fd, haptic_report(args.release_strength, 0, 0))
            time.sleep(0.4)
    finally:
        os.close(fd)
    print(f"Sent 3 pulses to {hid}.")


def byte(text):
    value = int(text, 0)
    if not 0 <= value <= 255:
        raise argparse.ArgumentTypeError("must be 0..255")
    return value


def main():
    p = argparse.ArgumentParser(description="Force Click haptics for Apple Magic Trackpad 2 on Linux.")
    p.add_argument("-t", "--threshold", type=byte, default=140,
                   help="finger pressure (0-253) that triggers the force click (default 140)")
    p.add_argument("--hysteresis", type=byte, default=20,
                   help="pressure drop below threshold needed to re-arm (default 20)")
    p.add_argument("--strength", type=byte, default=0x30, help="force pulse strength (default 0x30)")
    p.add_argument("--sharp", type=byte, default=0x10, help="sharp/click component, keep <= 0x20 (default 0x10)")
    p.add_argument("--deep", type=byte, default=0x10, help="deep/thud component, keep <= 0x40 (default 0x10)")
    p.add_argument("--release-strength", type=byte, default=0x14, help="release pulse strength (default 0x14)")
    p.add_argument("--no-release-pulse", dest="release_pulse", action="store_false",
                   help="do not play a pulse when easing off the force click")
    p.add_argument("--no-button", action="store_true",
                   help="trigger on pressure alone, without a normal click first")
    p.add_argument("--evdev", help="evdev node (default: autodetect)")
    p.add_argument("--hidraw", help="hidraw node (default: autodetect)")
    p.add_argument("-m", "--monitor", action="store_true", help="show live pressure bar (for calibration)")
    p.add_argument("-v", "--verbose", action="store_true", help="log every force click")
    p.add_argument("--test-pulse", action="store_true", help="play 3 pulses and exit")
    args = p.parse_args()
    if args.hysteresis >= args.threshold:
        p.error("--hysteresis must be smaller than --threshold")
    try:
        test_pulse(args) if args.test_pulse else run(args)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
