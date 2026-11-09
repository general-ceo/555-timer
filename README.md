# CLARUS

Two-factor login for CSE 484 (Group Clarus). The laptop checks a username and password, then sends a one-time code to the CLARUS board. The code shows up on the OLED, and the user has to type it on the keypad before the 555 timer runs out.

| File | Runs on |
|---|---|
| `laptop/clarus_login.py` | Laptop (Python 3 + pyserial) |
| `laptop/test_clarus_login.py` | Laptop (tests, no hardware needed) |
| `arduino/clarus/clarus.ino` | ItsyBitsy M0 Express (Arduino, C++) |

## How a login works

1. Enter a username and password on the laptop. They're checked against salted hashes in `users.json`.
2. The laptop makes a random 4-digit code and sends `CODE 1234` to the board.
3. The board shows the code on the OLED, starts the 555, and replies `STARTED`.
4. Type the code on the keypad (`*` clears, `#` submits). The laptop shows the digits as you type.
5. The board sends `ENTRY 1234`, or `TIMEOUT` (yellow LED) if the timer runs out first.
6. The laptop checks the code and replies `OK` (green LED) or `BAD` (red LED).
7. A wrong code or timeout gets a new code. After 3 failures there's a 60 s lockout.

If the board is missing, stops answering, or sends something unexpected, access is denied.

Messages go over USB serial at 9600 baud, one per line:

| Laptop to board | Board to laptop |
|---|---|
| `HELLO` | `CLARUS 1` |
| `CODE 1234` | `STARTED`, `TYPED 12`, `ENTRY 1234`, `TIMEOUT` |
| `OK`, `BAD`, `CANCEL` | `ERROR TIMER`, `ERROR UNKNOWN`, `ERROR TOO_LONG` |

## Wiring (ItsyBitsy M0 Express)

| Part | Connection |
|---|---|
| Keypad | rows 2, 3, 4, 7; columns 9, 10, 11, 12 |
| LEDs (220–330 Ω each) | red A3, yellow A4, green A5 |
| OLED (SSD1306, I2C) | SDA, SCL, 3V, G |
| NE555 (5 V from the USB pin) | pin 2 to A0, pin 4 to A1, pin 3 to 10 kΩ to A2, 20 kΩ from A2 to G |

The 10k/20k divider keeps A2 under 3.3 V, so don't wire the 555 output straight to the board. The timing resistor and capacitor go on pins 6 and 7, and the window is about 1.1 × R × C.

## Setup and use

Board:
1. In the Arduino IDE, add `https://adafruit.github.io/arduino-board-index/package_adafruit_index.json` under File > Preferences, then install **Adafruit SAMD Boards**.
2. Install the **Keypad** and **Adafruit SSD1306** libraries.
3. Open `arduino/clarus/clarus.ino`, select the ItsyBitsy M0 and its port, and upload. Close the Serial Monitor before running the laptop program.

Laptop (from the `laptop` folder):
```bash
pip install -r requirements.txt
python clarus_login.py --add-user
python clarus_login.py
```
`--port COM3` picks a port, and `--show-code` also prints the code on the laptop (testing only).

Tests: `python -m unittest test_clarus_login.py -v`

## Timer tuning

In the Serial Monitor (9600 baud, Newline), send `TIMERTEST`. The board prints the A2 reading and how long the window lasted (`WINDOW_MS=...`). It currently measures about 13.7 s; the goal is 20 s.
