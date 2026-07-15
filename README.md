# ets2-g29-macos

Make a **Logitech G29** actually usable on a **Mac** (Apple Silicon) with
**Euro Truck Simulator 2** running through **CrossOver / Wine** — full 900°
rotation and **speed-sensitive auto-centering**, with no Logitech software.

> Loose when you're parked → the wheel firms up the faster you drive → goes
> light again when you stop. The thing ETS2's force feedback would do, if force
> feedback survived CrossOver. It doesn't, so this fakes the important part.

## Why this exists

On macOS there is **no Logitech driver for the G-series wheels** — Logitech G HUB
only supports mice, keyboards and headsets, and it won't install inside a
CrossOver/Wine bottle either. So a G29 on a Mac:

- boots in a **restricted rotation range** (steering maxes out after ~90° of turn),
- has a permanent **auto-centering spring** fighting you even when parked,
- and gets **no force feedback** at all through CrossOver.

This tool talks to the wheel **directly over USB HID** — the same commands the
Linux [`new-lg4ff`](https://github.com/berarma/new-lg4ff) / Oversteer projects
use — to fix the range and the centering, no driver required. For the
speed-sensitive part it reads the truck's live speed straight from ETS2's
telemetry.

## What it does

| Command | Effect |
|---|---|
| `g29.py range [deg]` | Set steering rotation range (default **900°**) |
| `g29.py center <0-100>` | Set a fixed centering-spring strength |
| `g29.py off` | Disable the centering spring (fully free wheel) |
| `g29.py auto` | **Speed-sensitive centering daemon** (the main event) |
| `g29.py find-speed` | Calibrate the telemetry speed offset for your version |

## Requirements

- macOS (built and tested on Apple Silicon, macOS 15)
- A Logitech **G29** (VID `046d`, PID `c24f`). Other wheels use a different FFB
  protocol — see [Other wheels](#other-wheels).
- `hidapi`:
  ```sh
  brew install hidapi
  ```
- **For `auto` only:** ETS2 (the Windows build, running in CrossOver) with the
  SCS telemetry plugin installed — see below.

## Install

```sh
git clone https://github.com/producedbytmsh/ets2-g29-macos.git
cd ets2-g29-macos
brew install hidapi
```

No Python packages needed — it calls `libhidapi` directly via `ctypes` (this
sidesteps macOS SIP stripping the library path from the system Python).

### Telemetry plugin (only for `auto`)

`auto` reads your speed from the [RenCloud SCS SDK
plugin](https://github.com/RenCloud/scs-sdk-plugin). Install its **64-bit**
`scs-telemetry.dll` into ETS2's plugin folder **inside your CrossOver bottle**:

```
.../steamapps/common/Euro Truck Simulator 2/bin/win_x64/plugins/scs-telemetry.dll
```

(Create the `plugins` folder if it doesn't exist.) Launch ETS2 and click **OK**
on the *"Request to use advanced SDK features detected"* prompt.

CrossOver's Wine conveniently backs the plugin's shared memory as a normal file
under `/tmp/.wine-$UID/server-*/`, which this tool reads — so there's no Windows
helper to run.

## Usage

Plug in the wheel, launch ETS2, load into a drive, then:

```sh
# one-shots
python3 g29.py range 900        # full 900° rotation
python3 g29.py range 540        # tighter/faster steering
python3 g29.py off              # free-spinning wheel
python3 g29.py center 30        # constant 30% centering

# the good one — speed-sensitive centering (also sets 900° on start)
python3 g29.py auto
```

Leave `auto` running while you play; `Ctrl-C` stops it and releases the spring.
Live status is written to `/tmp/g29_status.txt`.

> **Nothing is persistent.** The wheel forgets everything on unplug/power-cycle,
> so re-run after replugging. (Want it automatic? See
> [Auto-start](#auto-start-optional).)

### Tuning `auto`

```sh
python3 g29.py auto --max-strength 0.6 --max-kmh 90 --curve 0.5
```

- `--max-strength` (0–1): spring strength at/above `--max-kmh`. Higher = heavier.
- `--max-kmh`: speed at which the spring reaches maximum.
- `--curve`: ramp shape. `1.0` = linear; **`<1` = more bite at low speed**
  (0.5 = square-root, the default, feels good in cities *and* on the motorway).

Default ramp: 0% parked · ~20% at 10 km/h · ~34% at 30 · ~46% at 55 · 60% at 90.

## Calibration (`find-speed`)

The daemon reads speed from a fixed byte offset in the telemetry block
(**948** by default, verified on ETS2 1.60). If a future game/plugin version
moves it — you'll notice the centering not tracking your speed — recalibrate:

```sh
python3 g29.py find-speed
```

Keep ETS2 focused and drive a **stop → accelerate to ~60 → brake to stop**
profile when prompted. It prints the offset that traced your speed:

```sh
python3 g29.py auto --offset <that number>
```

## Auto-start (optional)

To run `auto` automatically, drop a LaunchAgent at
`~/Library/LaunchAgents/com.user.g29auto.plist` that runs
`python3 /path/to/g29.py auto`. It idles harmlessly ("no telemetry") until ETS2
is up and driving.

## How it works

- **Range / centering** are Logitech HID output reports sent to the wheel's
  joystick collection (interface 0, usage page `0x01` / usage `0x04`): range =
  `f8 81 <lo> <hi> …`, centering = `fe 0d …` + `14 …`, disable = `f5 …`. Bytes
  and the magnitude math come straight from `new-lg4ff`.
- Commands are sent **open → write → close** every time. The wheel is **never
  held open** — holding the HID device open starves the game of wheel/pedal
  input (learned the hard way).
- **Speed** is read from the RenCloud telemetry block that Wine mirrors to a
  `tmpmap-*` file; the block is found by content (`sdkActive==1`, `game==1`),
  so it survives Wine re-creating the mapping under a new name.

## Other wheels

Written and tested for the **G29**. The G25/G27/DFGT use the same lg4ff command
family and will likely work by changing `PID`. The **G920/G923** use a different
(TrueForce) protocol and are **not** supported by these exact commands.

## Troubleshooting

- **Wheel/pedals stop responding in-game** → a program is holding the HID device
  open. This tool avoids that; if you hacked in a persistent connection, don't.
- **`auto` says "no telemetry"** → not in a drive, plugin not enabled, or ETS2 is
  **paused because it lost focus** (it pauses when it's not the front window —
  normal; it resumes when you're driving).
- **Centering doesn't match speed** → wrong offset for your version; run
  `find-speed`.
- **`libhidapi not found`** → `brew install hidapi`.

## Credits

- [RenCloud/scs-sdk-plugin](https://github.com/RenCloud/scs-sdk-plugin) — the ETS2 telemetry plugin
- [berarma/new-lg4ff](https://github.com/berarma/new-lg4ff) & Oversteer — the Logitech HID command reference
- [libusb/hidapi](https://github.com/libusb/hidapi)

## Disclaimer

Not affiliated with or endorsed by Logitech or SCS Software. Sends unofficial
commands to your hardware; use at your own risk. They're the same commands the
Linux community has used for years, and everything resets on unplug.

## License

[MIT](LICENSE)
