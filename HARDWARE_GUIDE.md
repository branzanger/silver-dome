# Silver Dome Hardware Wiring Guide

Zero hardware experience required. Follow in order.

## Priority

**Phase 1: RF-Based Drone Detection** — Get the RTL-SDR producing real FFT data on the dashboard. This is the core demo: passive detection of 2.4 GHz emitters (phones, drones) with real-time spectrum visualization.

**Phase 2 (if time permits): Multi-Sensor Fusion with Physical Response** — Wire up PIR, camera, gimbal, and laser. Show confidence escalating across modalities (0.20 → 0.65 → 0.90) with a physical gimbal lock-on.

---

## What You Need (Besides Your Components)

Grab these from the hackathon supply table:
- **Jumper wires** (male-to-female, at least 20)
- **Breadboard** (the white/clear board with rows of holes)
- **Resistors**: 3x 220-330 ohm (for LEDs), 1x 1K ohm + 1x 2K ohm (for distance sensor voltage divider)
- **Power supply for stepper**: the A4988 driver board needs separate power for the motor (usually 12V, check your motor)

## The Pi 5 GPIO Header

Hold the Pi with the USB ports pointing toward you. The 40-pin header is on the left.
Pin 1 is top-left (closest to the corner of the board, usually marked with a small square on the PCB).

```
                    Pi 5 (USB ports facing you)
                    +---------+
            3.3V  1 | o   o | 2   5V
          GPIO 2  3 | o   o | 4   5V
          GPIO 3  5 | o   o | 6   GND
          GPIO 4  7 | o   o | 8   GPIO 14
             GND  9 | o   o | 10  GPIO 15
  PIR --> GPIO17 11 | o   o | 12  GPIO 18  <-- SERVO TILT
LASER --> GPIO27 13 | o   o | 14  GND
         GPIO 22 15 | o   o | 16  GPIO 23  <-- STEPPER STEP
            3.3V 17 | o   o | 18  GPIO 24  <-- STEPPER DIR
         GPIO 10 19 | o   o | 20  GND
          GPIO 9 21 | o   o | 22  GPIO 25  <-- STEPPER ENABLE
         GPIO 11 23 | o   o | 24  GPIO 8
             GND 25 | o   o | 26  GPIO 7
          GPIO 0 27 | o   o | 28  GPIO 1
LED GRN-> GPIO 5 29 | o   o | 30  GND
LED AMB-> GPIO 6 31 | o   o | 32  GPIO 12
LED RED-> GPIO13 33 | o   o | 34  GND
         GPIO 19 35 | o   o | 36  GPIO 16
         GPIO 26 37 | o   o | 38  GPIO 20  <-- DIST TRIG
             GND 39 | o   o | 40  GPIO 21  <-- DIST ECHO
                    +---------+
```

---

# Phase 1: RF-Based Drone Detection

This is the priority. Get this working first.

## Step 1: RTL-SDR USB Dongle (1 min)

**What it does**: RF sensor. Passively detects radio signals from drones, phones, and other 2.4 GHz emitters. Shows real-time FFT power spikes on the dashboard.

Just plug it into a USB port on the Pi. Attach the antenna.

Install the driver on the Pi:
```bash
sudo apt-get install -y rtl-sdr librtlsdr-dev
pip3 install pyrtlsdr
```

**Test it**: Walk a phone (with WiFi/Bluetooth on) near the antenna. You should see 2.4 GHz power spikes in the FFT output.

## Step 2: LEDs (10 min)

**What they do**: Green = all clear, Amber = RF anomaly detected, Red = high-confidence threat.

Each LED has two legs:
- **Long leg** = positive (anode) — connects through a resistor to the GPIO pin
- **Short leg** = negative (cathode) — connects to GND

**For each LED, on the breadboard:**
```
GPIO pin ---> [220 ohm resistor] ---> LED long leg (+)
                                       LED short leg (-) ---> GND
```

**Wiring (do this 3 times, once per LED):**

| LED Color | GPIO | Pi Physical Pin | GND Pin |
|-----------|------|-----------------|---------|
| Green     | GPIO 5  | Pin 29 | Pin 30 |
| Amber     | GPIO 6  | Pin 31 | Pin 30 |
| Red       | GPIO 13 | Pin 33 | Pin 34 |

If you don't have 220 ohm resistors, anything from 100-470 ohm works. The LED will just be dimmer or brighter.

**Test it**: `python3 -m display.indicators`

## Step 3: Dashboard (2 min)

No wiring needed. Start the system and open the dashboard:
```bash
SILVER_DOME_SIMULATE=false python3 main.py --site-id SHACK15
# Open http://<pi-ip>:8080
```

You should see the radar display with real-time RF spectrum data. Walk a phone near the antenna to see detection events appear.

---

# Phase 2: Multi-Sensor Fusion + Gimbal Lock-On

Only start this after Phase 1 is solid. This adds PIR, camera, distance, and the physical gimbal so the system can correlate multiple sensors and physically point at a threat.

## Step 4: PIR Motion Sensor (5 min)

**What it does**: Detects movement via heat (infrared). Wide cone, ~7m range.

**Your sensor (HW-416)**: Has 3 pins. Look at the front of the board — the pins are labeled.

```
    HW-416 PIR
   +----------+
   |  (lens)  |
   |          |
   +--+--+--++
      |  |  |
     VCC OUT GND
```

**Wiring:**
| PIR Pin | Connects To | Pi Physical Pin |
|---------|-------------|-----------------|
| VCC     | 5V          | Pin 2           |
| OUT     | GPIO 17     | Pin 11          |
| GND     | GND         | Pin 6           |

Just plug 3 jumper wires:
1. Red wire: PIR VCC → Pi Pin 2 (5V)
2. Yellow wire: PIR OUT → Pi Pin 11 (GPIO 17)
3. Black wire: PIR GND → Pi Pin 6 (GND)

**Adjust the sensor**: There are two small orange screws (potentiometers) on the board.
- **Sensitivity**: Turn clockwise to max (detects more)
- **Delay**: Turn counter-clockwise to minimum (fastest response for demo)

**Test it**: `SILVER_DOME_SIMULATE=false python3 -m sensors.pir_sensor` — wave your hand in front of it.

## Step 5: HC-SR04 Distance Sensor (10 min)

**What it does**: Sends ultrasonic pings, measures how long they take to bounce back. Gives distance in meters.

**Your sensor**: The blue board with two silver cylinders ("eyes"). 4 pins: Vcc, Trig, Echo, Gnd.

```
    HC-SR04
   +--------+
   | (O)(O) |   <-- transducers
   |        |
   +--+--+--+--+
      |  |  |  |
     Vcc Trg Ech Gnd
```

**IMPORTANT: The Echo pin outputs 5V but the Pi expects 3.3V. You need a voltage divider or you WILL damage the Pi.**

**Option A — Voltage divider (proper way):**
On the breadboard:
```
Echo pin ---> [1K resistor] ---+--- wire to Pi GPIO 21 (Pin 40)
                               |
                          [2K resistor]
                               |
                              GND
```

**Option B — Single resistor (quick hack, works for demo):**
Just put a 1K resistor in series between Echo and GPIO 21. The Pi's internal clamping diodes handle the rest.
```
Echo pin ---> [1K resistor] ---> Pi GPIO 21 (Pin 40)
```

**Wiring:**
| HC-SR04 Pin | Connects To | Pi Physical Pin |
|-------------|-------------|-----------------|
| Vcc         | 5V          | Pin 4           |
| Trig        | GPIO 20     | Pin 38          |
| Echo        | GPIO 21 (through resistor!) | Pin 40 |
| Gnd         | GND         | Pin 39          |

**Test it**: `SILVER_DOME_SIMULATE=false python3 -m sensors.distance_sensor` — put your hand in front of the "eyes" at different distances.

## Step 6: Camera (1 min)

Plug the laptop webcam (USB) into the Pi. Or use the Pi Camera Module (ribbon cable).

Install on Pi:
```bash
pip3 install opencv-python ultralytics
```

## Step 7: Stepper Motor + A4988 Driver (15 min)

**What it does**: Pan axis. Rotates the distance sensor and laser to point at the target. 360 degrees with precise control.

**The A4988 is a small red/purple board.** It sits between the Pi and the motor. The Pi sends step/direction signals, the A4988 drives the motor.

```
        A4988 Driver Board
    +---------------------+
    | VMOT  GND           |  <-- Motor power (12V + GND)
    | 2B    2A            |  <-- Motor coil 2
    | 1A    1B            |  <-- Motor coil 1
    | VDD   GND           |  <-- Logic power (3.3V or 5V from Pi + GND)
    | STEP  DIR           |  <-- Control signals from Pi
    | SLP   RST           |  <-- tie these together (wire SLP to RST)
    | EN    MS3           |  <-- Enable pin
    | MS1   MS2           |  <-- Microstepping (leave unconnected for full step)
    +---------------------+
```

**Wiring:**

| A4988 Pin | Connects To | Notes |
|-----------|-------------|-------|
| VMOT      | 12V power supply + | Motor voltage (check your motor — could be 5V or 12V) |
| GND (top) | 12V power supply - AND Pi GND | **Both grounds must be connected together** |
| VDD       | Pi 3.3V (Pin 17) | Logic power |
| GND (bottom) | Pi GND (Pin 20) | Logic ground |
| STEP      | Pi GPIO 23 (Pin 16) | Step pulse |
| DIR       | Pi GPIO 24 (Pin 18) | Direction |
| EN        | Pi GPIO 25 (Pin 22) | Enable (active low) |
| SLP       | wire to RST | **Tie these two pins together with a short wire** |
| RST       | wire to SLP | (same wire, keeps driver awake) |
| 1A, 1B    | Motor coil 1 wires | Check motor datasheet for which wires are coil 1 |
| 2A, 2B    | Motor coil 2 wires | Check motor datasheet for which wires are coil 2 |

**CRITICAL: The motor power supply GND must connect to the Pi GND.** Without a common ground, the signals won't work.

**Motor wires**: Your stepper has 4 wires (bipolar) or 6 wires (unipolar). For 4-wire: pair them by checking which two have continuity (use a multimeter, or touch two wires together — if the motor shaft resists turning, those two are a pair). One pair goes to 1A+1B, the other to 2A+2B.

**Test it**: `python3 -m gimbal.controller` — the motor should turn through 90/180/270/360 degrees.

## Step 8: Servo Motor (5 min)

**What it does**: Tilt axis. Points up/down.

Servos have 3 wires (usually color-coded):
| Servo Wire | Color (typical) | Connects To | Pi Physical Pin |
|------------|-----------------|-------------|-----------------|
| Signal     | Orange/Yellow/White | GPIO 18 | Pin 12 |
| Power      | Red             | 5V          | Pin 2 or 4      |
| Ground     | Brown/Black     | GND         | Pin 6 or 9      |

## Step 9: Laser (2 min)

**What it does**: Points at the target when locked on. Just turns on/off.

If it's a simple laser module with 2 pins (+ and -):
| Laser Pin | Connects To | Pi Physical Pin |
|-----------|-------------|-----------------|
| + (or S)  | GPIO 27     | Pin 13          |
| - (or GND)| GND         | Pin 14          |

If it's a laser module with 3 pins (S, +, -):
| Laser Pin | Connects To | Pi Physical Pin |
|-----------|-------------|-----------------|
| S (signal)| GPIO 27     | Pin 13          |
| + (VCC)   | 3.3V        | Pin 17          |
| - (GND)   | GND         | Pin 14          |

---

## Physical Assembly

Mount everything on a piece of cardboard, a board, or whatever you have:

```
        TOP VIEW (looking down)

              [Camera]
                 |
    [Laser] -- [Servo] -- [HC-SR04]
                 |
             [Stepper shaft]
                 |
          +-----------+
          |  Stepper   |
          |  Motor     |
          +-----------+
                 |
    [PIR sensor]   [Breadboard with LEDs]
                 |
          +-----------+
          | Pi 5      |
          |     [USB: RTL-SDR]
          +-----------+
```

The stepper is fixed to the base. Its shaft holds a platform with the distance sensor, laser, and camera. When the stepper rotates, those all sweep together.

The PIR is fixed to the base (wide area tripwire, doesn't need to move).

---

## Deploy Software

On the Pi:
```bash
git clone https://github.com/Mukund2/Silver-Dome.git
cd Silver-Dome
pip3 install -r requirements.txt
python3 main.py --site-id SHACK15
```

Dashboard opens at `http://<pi-ip>:8080`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "No module RPi.GPIO" | `sudo apt-get install python3-rpi.gpio` or `pip3 install RPi.GPIO` |
| Motor doesn't move | Check SLP-RST are tied together. Check motor power supply. Check common GND. |
| Motor vibrates but doesn't turn | Motor coil wires are wrong pairing. Swap one pair (e.g., swap 1A and 2A). |
| Distance sensor reads -1 | Check voltage divider on Echo. Check Trig/Echo pin assignment. |
| PIR fires constantly | Turn sensitivity pot down. Move away from heat sources. |
| LEDs don't light | Check polarity (long leg = +). Check resistor is connected. |
| RTL-SDR not found | Run `rtl_test` to check. Try a different USB port. Check `lsusb` for the device. |
| Dashboard blank | Check firewall. Try `http://localhost:8080` on the Pi itself. |
