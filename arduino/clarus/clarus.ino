/*
  CLARUS hardware (starter code). Arduino sketches are C++.

  This board's job: show the one-time code from the laptop on the OLED, start the 555 timer,
  collect the keypad entry, send it to the laptop, and light the LEDs. The laptop decides
  whether the code is right; this board never checks it.

  Messages over the USB cable (serial, 9600 baud, one per line; full list in the README):
    laptop -> board:  HELLO | CODE 1234 | OK | BAD | CANCEL
    board -> laptop:  CLARUS 1 | STARTED | ENTRY 1234 | TIMEOUT | ERROR <what>

  Parts are not final (TBD). Written for an Arduino Uno, a 4x4 membrane keypad, and a
  128x64 I2C OLED (SSD1306). Set USE_OLED to 0 to build without the OLED, then run the
  laptop program with --show-code.

  Libraries (Arduino IDE > Tools > Manage Libraries):
    "Keypad" by Mark Stanley and Alexander Brevig
    "Adafruit SSD1306" (also installs "Adafruit GFX Library"), only if USE_OLED is 1
*/

#ifndef USE_OLED
#define USE_OLED 1    // change to 0 to build without the OLED
#endif

#include <Keypad.h>
#if USE_OLED
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#endif

// ---------- settings ----------
const int CODE_DIGITS = 4;                 // TBD: must match CODE_DIGITS in clarus_login.py
const unsigned long RESULT_SHOW_MS = 3000; // how long the green, red or yellow light stays on
const unsigned long MAX_WINDOW_MS = 28000; // backstop if the 555 never turns off (wiring fault).
                                           // Longer than the 20 s window plus tolerance, but
                                           // shorter than the laptop's own 30 s limit.
const unsigned long MAX_WAIT_MS = 5000;    // give up on the laptop's OK/BAD after this long

// ---------- pins (example wiring; see the README) ----------
const byte ROWS = 4;
const byte COLS = 4;
char keys[ROWS][COLS] = {
  {'1', '2', '3', 'A'},
  {'4', '5', '6', 'B'},
  {'7', '8', '9', 'C'},
  {'*', '0', '#', 'D'}
};
byte rowPins[ROWS] = {2, 3, 4, 5};
byte colPins[COLS] = {6, 7, 8, 9};
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

const int RED_LED = 10;
const int YELLOW_LED = 11;
const int GREEN_LED = 12;
const int TIMER_TRIGGER = A0;  // to 555 pin 2 (TRIG): a short LOW pulse starts the 20 s window
const int TIMER_RESET = A1;    // to 555 pin 4 (RESET): LOW stops the timer
const int TIMER_OUTPUT = A2;   // from 555 pin 3 (OUT): HIGH while the window is open
// D0 and D1 carry the USB serial messages, so nothing else may use them.
// The OLED uses A4 (SDA) and A5 (SCL).

#if USE_OLED
Adafruit_SSD1306 display(128, 64, &Wire, -1);
bool oledOk = false;
#endif

// The Keypad library already uses the names IDLE, PRESSED, HOLD and RELEASED, so these get a prefix.
enum BoardState { ST_IDLE, ST_ENTERING, ST_WAITING, ST_RESULT };
BoardState state = ST_IDLE;

char code[CODE_DIGITS + 1];   // only for showing it; the laptop keeps its own copy and checks
char typed[CODE_DIGITS + 1];
int typedCount = 0;
unsigned long timerStartedAt = 0;
unsigned long resultShownAt = 0;
unsigned long waitingSince = 0;

char lineBuf[24];             // longest message we accept from the laptop
int lineLen = 0;
bool lineTooLong = false;

// ---------- display ----------
void showMessage(const __FlashStringHelper *message) {
#if USE_OLED
  if (!oledOk) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.println(message);
  display.display();
#else
  (void)message;                    // no OLED in this build
#endif
}

void showCodeScreen() {
#if USE_OLED
  if (!oledOk) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(F("Code:"));
  display.setTextSize(3);
  display.setCursor(0, 12);
  display.println(code);
  display.setTextSize(2);
  display.setCursor(0, 44);
  for (int i = 0; i < CODE_DIGITS; i++) {
    display.print(i < typedCount ? typed[i] : '_');
  }
  display.display();
#endif
}

// ---------- lights ----------
void lightsOff() {
  digitalWrite(RED_LED, LOW);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(GREEN_LED, LOW);
}

void showResult(int ledPin, const __FlashStringHelper *message) {
  lightsOff();
  digitalWrite(ledPin, HIGH);
  showMessage(message);
  resultShownAt = millis();
  state = ST_RESULT;
}

// ---------- 555 timer ----------
void stopTimer() {
  digitalWrite(TIMER_RESET, LOW);   // holds the 555 off and empties its capacitor
}

bool startTimer() {
  stopTimer();
  delay(20);                        // let the capacitor empty if a window was still running
  digitalWrite(TIMER_RESET, HIGH);
  digitalWrite(TIMER_TRIGGER, LOW); // short LOW pulse on pin 2 starts the one-shot
  delay(1);
  digitalWrite(TIMER_TRIGGER, HIGH);
  delay(5);
  timerStartedAt = millis();
  return digitalRead(TIMER_OUTPUT) == HIGH;   // the window should now be open
}

// ---------- states ----------
void goIdle() {
  stopTimer();
  lightsOff();
  typedCount = 0;
  typed[0] = '\0';
  state = ST_IDLE;
  showMessage(F("CLARUS\nready"));
}

void beginAttempt(const char *newCode) {
  memcpy(code, newCode, CODE_DIGITS);
  code[CODE_DIGITS] = '\0';
  typedCount = 0;
  typed[0] = '\0';
  lightsOff();
  if (!startTimer()) {              // fail closed: no timer, no attempt
    stopTimer();
    state = ST_IDLE;
    showMessage(F("Timer\nfault"));
    Serial.println(F("ERROR TIMER"));
    return;
  }
  state = ST_ENTERING;
  showCodeScreen();
  Serial.println(F("STARTED"));
}

// ---------- keypad ----------
void handleKey(char key) {
  if (state != ST_ENTERING) return;          // keys only count while a code is active
  if (key >= '0' && key <= '9') {
    if (typedCount < CODE_DIGITS) {          // extra digits are ignored
      typed[typedCount++] = key;
      typed[typedCount] = '\0';
    }
  } else if (key == '*') {                   // TBD: * clears
    typedCount = 0;
    typed[0] = '\0';
  } else if (key == '#') {                   // TBD: # sends, only once all digits are in
    if (typedCount == CODE_DIGITS) {
      stopTimer();
      state = ST_WAITING;
      waitingSince = millis();
      Serial.print(F("ENTRY "));
      Serial.println(typed);
      showMessage(F("Checking"));
      return;
    }
  }
  // A, B, C and D are ignored.
  showCodeScreen();
}

// ---------- messages from the laptop ----------
bool isDigits(const char *s, int n) {
  for (int i = 0; i < n; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
  }
  return s[n] == '\0';
}

void handleCommand(const char *cmd) {
  if (strcmp(cmd, "HELLO") == 0) {
    Serial.println(F("CLARUS 1"));
  } else if (strncmp(cmd, "CODE ", 5) == 0 && isDigits(cmd + 5, CODE_DIGITS)) {
    beginAttempt(cmd + 5);
  } else if (strcmp(cmd, "OK") == 0 && state == ST_WAITING) {
    showResult(GREEN_LED, F("Access\ngranted"));
  } else if (strcmp(cmd, "BAD") == 0 && state == ST_WAITING) {
    showResult(RED_LED, F("Wrong\ncode"));
  } else if (strcmp(cmd, "CANCEL") == 0) {
    goIdle();
  } else {
    Serial.println(F("ERROR UNKNOWN"));
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (lineTooLong) {
        Serial.println(F("ERROR TOO_LONG"));
      } else {
        lineBuf[lineLen] = '\0';
        handleCommand(lineBuf);
      }
      lineLen = 0;
      lineTooLong = false;
    } else if (lineLen < (int)sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineTooLong = true;
    }
  }
}

// ---------- setup and main loop ----------
void setup() {
  Serial.begin(9600);
  pinMode(RED_LED, OUTPUT);
  pinMode(YELLOW_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(TIMER_TRIGGER, OUTPUT);
  digitalWrite(TIMER_TRIGGER, HIGH);  // pin 2 must sit HIGH except during the start pulse
  pinMode(TIMER_RESET, OUTPUT);
  pinMode(TIMER_OUTPUT, INPUT);
#if USE_OLED
  // 0x3C is the usual I2C address; some OLED boards use 0x3D. If no OLED answers, the board
  // still works, and the laptop program can show the code instead (--show-code).
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
#endif
  goIdle();
}

void loop() {
  readSerial();

  char key = keypad.getKey();
  if (key != NO_KEY) handleKey(key);

  if (state == ST_ENTERING) {
    unsigned long elapsed = millis() - timerStartedAt;
    bool timerDone = elapsed > 50 && digitalRead(TIMER_OUTPUT) == LOW;
    if (timerDone || elapsed > MAX_WINDOW_MS) {
      stopTimer();
      Serial.println(F("TIMEOUT"));
      showResult(YELLOW_LED, F("Time is\nup"));
    }
  }

  if (state == ST_RESULT && millis() - resultShownAt > RESULT_SHOW_MS) {
    goIdle();
  }

  if (state == ST_WAITING && millis() - waitingSince > MAX_WAIT_MS) {
    goIdle();                        // the laptop never answered; no light means no access
  }
}
