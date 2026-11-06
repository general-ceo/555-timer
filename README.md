# CLARUS starter code

Starter code for the team's current rough flow (11/05, **not final**). The laptop makes and checks the one-time code. The hardware shows it on the OLED, runs the 555 timer, reads the keypad, and lights the LEDs.

| File | Runs on | Language | Proposed owner |
|---|---|---|---|
| `laptop/clarus_login.py` | Laptop | Python 3 + pyserial | Julianna |
| `laptop/test_clarus_login.py` | Laptop | Python (tests, no hardware needed) | Julianna |
| `arduino/clarus/clarus.ino` | Arduino | C++ (Arduino) | Maceo (keypad), Seamus (OLED, timer, LEDs) |

Values marked **TBD** in the code are team decisions that haven't been made yet.

## How a login works

1. The laptop program asks for a username and password (factor 1) and checks them against `users.json`. That file holds only salted hashes, never passwords.
2. The laptop makes a random 4-digit code with Python's `secrets` module and sends `CODE 4821` down the USB cable.
3. The Arduino shows the code on the OLED, starts the 555, and answers `STARTED`.
4. The user types the digits on the keypad. `*` clears and `#` sends. The Arduino sends `ENTRY 4821`.
5. If the 555 runs out first, the Arduino lights **yellow** and sends `TIMEOUT`.
6. The laptop compares the entry with its code and sends `OK` (Arduino lights **green**) or `BAD` (Arduino lights **red**).
7. After a wrong code or a timeout, the laptop sends a new code. After 3 failures (TBD), it locks out for 60 s (TBD) and goes back to the password prompt.

**Fail closed:** the laptop denies access in all of these cases:
- the hardware isn't found
- the hardware doesn't answer
- the hardware sends anything other than these exact messages
- the cable is pulled

### Messages (one line of text each, 9600 baud)

| Direction | Message | Meaning |
|---|---|---|
| Laptop → Arduino | `HELLO` | Are you the CLARUS board? |
| Arduino → Laptop | `CLARUS 1` | Yes. This only finds the right port; it is not security. |
| Laptop → Arduino | `CODE 4821` | Show this code and start the timer |
| Arduino → Laptop | `STARTED` | Timer running, code on the display |
| Arduino → Laptop | `ENTRY 4821` | What the user typed (sent on `#`) |
| Arduino → Laptop | `TIMEOUT` | The 555 ran out; yellow is on |
| Laptop → Arduino | `OK` / `BAD` | Light green / red |
| Laptop → Arduino | `CANCEL` | Stop and go back to idle |
| Arduino → Laptop | `ERROR TIMER` / `ERROR UNKNOWN` / `ERROR TOO_LONG` | Timer didn't start / unknown message / message too long |

## Wiring: Adafruit ItsyBitsy M0 or M4 (the team's board)

The other parts are still examples: a **4x4 membrane keypad** and a **128x64 I2C OLED (SSD1306)**. One USB cable powers everything and carries the messages. The sketch picks the ItsyBitsy pins automatically when you build for an ItsyBitsy.

**The ItsyBitsy runs at 3.3 V.** No pin may ever see more than 3.3 V, so the 555 needs care (below).

| Part | Connects to |
|---|---|
| Keypad, 8 wires | Rows → #7, #9, #10, #11; columns → #12, #2, #3, #4 (#2–#4 are on the short end of the board). Pinouts differ between keypads; if keys come out wrong, change the order of `rowPins` / `colPins`. |
| Red / yellow / green LED | A3 / A4 / A5 → 220–330 Ω resistor → LED long leg; LED short leg → G |
| OLED, 4 pins | VCC → 3V, GND → G, SDA → SDA, SCL → SCL. The ItsyBitsy has no pull-up resistors on SDA/SCL; most OLED modules include them. |
| Leave free | #5 (output only) and #13 (onboard LED) |

**555 timer: pick one.**

*Option A, a standard NE555.* It needs at least 4.5 V, so run it from the **USB** pin (5 V):

| 555 pin | Connects to |
|---|---|
| 1 (GND) | G |
| 2 (TRIG) | A0. The board's 3.3 V holds it off; a LOW pulse starts the window. |
| 3 (OUT) | **Through a voltage divider:** pin 3 → 10 kΩ → A2, and A2 → 20 kΩ → G. Never wire pin 3 straight to A2, because 5 V can damage it. |
| 4 (RESET) | A1 |
| 5 (CTRL) | 10 nF capacitor → G |
| 6 + 7 | Join them. Resistor R up to USB (5 V); capacitor C down to G. On an electrolytic capacitor, the leg marked "−" goes to G. |
| 8 (VCC) | USB (5 V) |

*Option B, a CMOS 555* (TLC555, LMC555, or ICM7555). These run from **3V**. Wire it as in Option A, but connect pins 8 and R to 3V instead of USB, and wire pin 3 straight to A2 with no divider.

**Window length:** about 1.1 × R × C either way. For example, 180 kΩ with 100 µF gives about 19.8 s. Capacitors are often ±20%, so time it on the breadboard. Natalia is confirming the values.

### Arduino Uno (alternative)

If the team switches to an Uno, the sketch uses these pins instead. Everything runs off the Uno's **5V** and **GND**, and no divider is needed.

| Part | Connects to |
|---|---|
| Keypad, 8 wires | Rows → D2, D3, D4, D5; columns → D6, D7, D8, D9. Pinouts differ between keypads; if keys come out wrong, change the order of `rowPins` / `colPins`. |
| Red / yellow / green LED | D10 / D11 / D12 → 220 Ω resistor → LED long leg; LED short leg → GND |
| OLED, 4 pins | VCC → 5V (check that your module takes 5 V), GND → GND, SDA → A4, SCL → A5 |
| 555 pin 1 (GND) | GND |
| 555 pin 2 (TRIG) | A0. The Arduino pulses it LOW to start the window. |
| 555 pin 3 (OUT) | A2. HIGH while the window is open. |
| 555 pin 4 (RESET) | A1. The Arduino holds it LOW to stop the timer. |
| 555 pin 5 (CTRL) | 10 nF capacitor → GND (keeps the timing steady) |
| 555 pins 6 + 7 | Join them. Resistor R up to 5V; capacitor C down to GND. On an electrolytic capacitor, the leg marked "−" goes to GND. |
| 555 pin 8 (VCC) | 5V |
| D0, D1 | **Leave empty.** They carry the USB messages. |

On an Uno, if the board shows "Timer fault" and the wiring looks right, measure pin 3 while the timer runs. A standard NE555's HIGH output can sit near the bottom of what the Uno reads as HIGH. A CMOS 555 (TLC555 or LMC555) outputs a full 5 V.

## Running it

**Arduino**
1. Install the Arduino IDE.
2. **ItsyBitsy only:**
   1. Under File > Preferences > "Additional boards manager URLs", add `https://adafruit.github.io/arduino-board-index/package_adafruit_index.json`.
   2. In Tools > Board > Boards Manager, install **Adafruit SAMD Boards**.
3. In Tools > Manage Libraries, install **Keypad** (by Mark Stanley and Alexander Brevig) and **Adafruit SSD1306**, which also installs Adafruit GFX.
4. Open `arduino/clarus/clarus.ino`. In Tools > Board, pick the ItsyBitsy M0 or M4 entry, whichever is printed on your board, then pick the port and click **Upload**. If no port shows up, double-tap the ItsyBitsy's reset button to put it into upload mode, and check that the USB cable carries data.
5. No OLED yet? Set `USE_OLED` to `0` at the top of the sketch.

The sketch compiles for an Arduino Uno, with and without the OLED. It was checked on 11/05 with Arduino AVR Boards 1.8.8, Keypad 3.1.1, and Adafruit SSD1306 2.5.17. It also compiles for the ItsyBitsy M0 Express and the ItsyBitsy M4, with and without the OLED (checked 11/06 with Adafruit SAMD Boards 1.7.17). The ItsyBitsy pins were checked against Adafruit's board definitions. Nothing has run on real hardware yet.

The ItsyBitsy M0 and M4 have plenty of memory. On an Uno, the OLED's screen buffer takes 1 KB of the board's 2 KB memory at startup, which leaves only about 270 bytes free. That's enough for this sketch, but keep text in `F("...")` and avoid large arrays or `String`. If the OLED stays blank, low memory is one possible cause.

**Test the board by itself:**
1. Open Tools > Serial Monitor and set it to 9600 baud with **Newline** line ending.
2. Type `HELLO`. Expect `CLARUS 1`.
3. Type `CODE 1234`. Expect `STARTED`, with the code on the OLED.
4. On the keypad, press `1 2 3 4 #`. Expect `ENTRY 1234`.
5. Type `OK` for green or `BAD` for red. Or wait about 20 s for `TIMEOUT` and yellow.
6. Close the Serial Monitor afterward. Only one program can use the port at a time.

**Laptop** (from a terminal, in the `laptop` folder)

Install pyserial:
```bash
pip install -r requirements.txt
```

Create a demo user (only a salted hash goes into `users.json`):
```bash
python clarus_login.py --add-user
```

Log in. Add `--show-code` to print the code on the laptop instead of the OLED, or `--port COM3` to skip the port search:
```bash
python clarus_login.py
```

Run the tests. They use a fake device and need no hardware:
```bash
python -m unittest test_clarus_login.py -v
```

There is deliberately no switch that skips the hardware in the real program. A switch like that would be a way around the second factor. The fake device exists only in the tests.

## Still TBD (team decisions)

- OLED or laptop screen for showing the code (both supported: `USE_OLED` and `--show-code`)
- Board, keypad, and OLED models
- Code length (`CODE_DIGITS`, in both files)
- What `*` and `#` do
- Lockout counts and length
- What the push buttons do (this code doesn't use them yet)
- The 555's R and C values

## Known limitations (for the report)

- **No device authentication.** A program on the laptop could pretend to be the board, because the code travels in plain text over the cable. Future work: a secret key shared by the board and the laptop, with a keyed-hash (HMAC) challenge and response.
- **Local access defeats it.** Anyone who can run or edit programs on the laptop can skip `clarus_login.py`. Guarding the real computer login would mean hooking into the operating system's login, which is out of scope.
- **Lockouts reset on restart.** The failure counts live only while the program runs.
- **Anyone at the board can pass.** Someone who can see the OLED and also has the password gets in.
- **Don't hand in real passwords.** Use a demo password for any `users.json` you submit.
