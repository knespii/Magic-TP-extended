# Magic-TP-extended

Force Click haptics for the **Apple Magic Trackpad 2** on Linux.

On macOS, pressing harder after a normal click gives a second, deeper haptic
"click" (Force Click). Linux ignores that. `mtp-forced` adds the feel of it
back: a small userspace daemon watches finger pressure and, once you push past
a threshold, fires an extra pulse from the trackpad's haptic motor.

> Status: early. For now it only does the haptic feedback. It doesn't trigger
> any action yet (see [Roadmap](#roadmap)).

## How it works

- **Normal clicking stays as it is.** The trackpad firmware and the kernel
  driver `hid-magicmouse` handle the regular click and its haptic. The daemon
  does not switch the trackpad into "host click" mode, so if the daemon stops,
  nothing breaks.
- The daemon reads per-finger pressure (`ABS_MT_PRESSURE`, 0–253) from the
  trackpad's `/dev/input/event*` node.
- When the button is held down and pressure crosses the threshold, it writes a
  15-byte output report to the trackpad's `/dev/hidraw*` node, which plays
  one actuator pulse:

  ```
  F2 53 01 SS 78 02 CC 24 30 06 01 DD 18 48 12
           │        │              └─ deep / thud component
           │        └─ sharp / click component
           └─ overall strength
  ```

- Easing off below `threshold - hysteresis` plays a softer release pulse and
  re-arms the force click.

Only the Python 3 standard library is needed. No kernel module, no
dependencies.

## Supported hardware

| Device | Connection | Status |
|---|---|---|
| Magic Trackpad 2 (`004C:0265`) | Bluetooth | primary target (Debian 13, kernel 6.12) |
| Magic Trackpad 2 (`05AC:0265`) | USB cable | untested; USB may need a different transport |
| Magic Trackpad USB-C (`0324`) | BT / USB | untested |

## Installation

```bash
git clone https://github.com/knespii/Magic-TP-extended.git
cd Magic-TP-extended
```

1. **Give your user access to the trackpad's hidraw node** (one-time, needs root):

   ```bash
   sudo install -m 644 udev/70-magic-trackpad.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   sudo udevadm trigger --subsystem-match=hidraw
   ```

   Your user also needs to be in the `input` group to read pressure from
   `/dev/input` (`sudo usermod -aG input $USER`, then log out and back in).

2. **Try it:**

   ```bash
   ./mtp-forced.py --test-pulse      # the trackpad should tap 3 times
   ./mtp-forced.py --monitor -v      # live pressure bar, logs force clicks
   ```

3. **Run it automatically** as a systemd user service:

   ```bash
   install -Dm755 mtp-forced.py ~/.local/bin/mtp-forced
   install -Dm644 systemd/mtp-forced.service ~/.config/systemd/user/mtp-forced.service
   systemctl --user daemon-reload
   systemctl --user enable --now mtp-forced
   ```

   The daemon waits for the trackpad and reconnects on its own when you turn
   it off and on, or when Bluetooth drops.

## Tuning

Use `--monitor` to see how much pressure your normal and deep presses produce,
then set the threshold between them.

Reference values measured on a Bluetooth Magic Trackpad 2: a normal click
peaks at a pressure of about 64–77, a deliberate hard press at 117–253. The
default threshold of 140 sits between them and re-arms below 100.

| Option | Default | Meaning |
|---|---|---|
| `-t, --threshold` | `140` | Pressure (0–253) that triggers the force click |
| `--hysteresis` | `40` | How far pressure must drop below the threshold to re-arm |
| `--strength` | `0x30` | Force pulse strength |
| `--sharp` | `0x10` | Sharp "click" component (keep ≤ `0x20`) |
| `--deep` | `0x10` | Deep "thud" component (keep ≤ `0x40`) |
| `--release-strength` | `0x14` | Release pulse strength |
| `--no-release-pulse` | | No pulse when easing off |
| `--no-button` | | Trigger on pressure alone, without a normal click first |
| `--evdev`, `--hidraw` | auto | Pick the device nodes manually |

To change options for the service, edit `ExecStart=` in
`~/.config/systemd/user/mtp-forced.service`, for example
`ExecStart=%h/.local/bin/mtp-forced -t 190 --strength 0x40`.

## Troubleshooting

- **`Permission denied: /dev/hidrawN`**: the udev rule isn't active. Check
  `ls -l /dev/hidraw*` and re-run the `udevadm trigger` step, or reconnect the
  trackpad.
- **`Waiting for Magic Trackpad...` forever**: check that the trackpad is
  connected and that `hid_magicmouse` is loaded (`lsmod | grep magicmouse`).
- **Force click fires too easily, or never**: adjust `--threshold` using
  `--monitor`.

## Roadmap

- [x] Force Click haptic feedback
- [ ] Trigger an action on force click (key combo, command, middle click…)
- [ ] Config file
- [ ] Verify USB and USB-C trackpads
- [ ] Haptic feedback for other gestures (snapping, scroll detents)

## Credits

The haptic report format comes from earlier reverse-engineering work:

- [nexustar/linux-hid-magicmouse](https://github.com/nexustar/linux-hid-magicmouse): kernel driver patch with host-click mode and custom vibration
- [Eriskii/LinuxMagicForce](https://github.com/Eriskii/LinuxMagicForce): userspace force-touch daemon in Rust

## License

MIT
