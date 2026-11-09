// CLARUS board sketch (Adafruit ItsyBitsy M0 Express; also builds for an Arduino Uno).
// Shows the laptop's code on the OLED, runs the 555 window, reads the keypad,
// sends the entry back, and lights the LEDs. The laptop decides if the code is right.
//
// Serial, 9600 baud, one message per line:
//   laptop -> board: HELLO, CODE 1234, OK, BAD, CANCEL
//   board -> laptop: CLARUS 1, STARTED, TYPED 12, ENTRY 1234, TIMEOUT, ERROR <reason>
//
// Libraries: Keypad, Adafruit SSD1306, Adafruit GFX

#ifndef USE_OLED
#define USE_OLED 1   // set to 0 to build without the OLED
#endif

#include <Keypad.h>
#if USE_OLED
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#endif

const int CODE_DIGITS = 4;                  // must match clarus_login.py
const unsigned long RESULT_SHOW_MS = 3000;  // how long a result LED stays on
const unsigned long MAX_WINDOW_MS = 28000;  // backstop if the 555 never turns off
const unsigned long MAX_WAIT_MS = 5000;     // give up waiting for OK/BAD

const byte ROWS = 4;
const byte COLS = 4;
char keys[ROWS][COLS] = {
  {'1', '2', '3', 'A'},
  {'4', '5', '6', 'B'},
  {'7', '8', '9', 'C'},
  {'*', '0', '#', 'D'}
};
#if defined(ARDUINO_ARCH_SAMD)
// ItsyBitsy (3.3 V logic)
byte rowPins[ROWS] = {2, 3, 4, 7};
byte colPins[COLS] = {9, 10, 11, 12};
const int RED_LED = A3;
const int YELLOW_LED = A4;
const int GREEN_LED = A5;
#else
// Uno
byte rowPins[ROWS] = {2, 3, 4, 5};
byte colPins[COLS] = {6, 7, 8, 9};
const int RED_LED = 10;
const int YELLOW_LED = 11;
const int GREEN_LED = 12;
#endif
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

const int TIMER_TRIGGER = A0;  // 555 pin 2
const int TIMER_RESET = A1;    // 555 pin 4
const int TIMER_OUTPUT = A2;   // 555 pin 3, through the 10k/20k divider

#if USE_OLED
Adafruit_SSD1306 display(128, 64, &Wire, -1);
bool oledOk = false;
#endif

// ST_ prefix because the Keypad library already uses IDLE, PRESSED, etc.
enum BoardState { ST_IDLE, ST_ENTERING, ST_WAITING, ST_RESULT };
BoardState state = ST_IDLE;

char code[CODE_DIGITS + 1];   // only for display; the laptop keeps its own copy
char typed[CODE_DIGITS + 1];
int typedCount = 0;
unsigned long timerStartedAt = 0;
unsigned long resultShownAt = 0;
unsigned long waitingSince = 0;

char lineBuf[24];
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
  (void)message;
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

// ---------- LEDs ----------
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
  digitalWrite(TIMER_RESET, LOW);
}

bool timerOutputHigh() { return analogRead(TIMER_OUTPUT) > 500; }  // about 1.6 V

bool startTimer() {
  stopTimer();
  delay(20);                        // let the capacitor discharge
  digitalWrite(TIMER_RESET, HIGH);
  digitalWrite(TIMER_TRIGGER, LOW); // short LOW pulse starts the one-shot
  delay(1);
  digitalWrite(TIMER_TRIGGER, HIGH);
  delay(5);
  timerStartedAt = millis();
  return timerOutputHigh();
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
  if (!startTimer()) {              // no timer, no attempt
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

// TIMERTEST (from the Serial Monitor): measures how long the 555 window lasts.
// Prints the A2 reading, then WINDOW_MS, NO_OUTPUT, or NEVER_OFF. Never grants access.
void timerTest() {
  goIdle();
  startTimer();
  unsigned long t0 = timerStartedAt;
  int raw = analogRead(TIMER_OUTPUT);
  Serial.print(F("TIMERTEST A2="));
  Serial.println(raw);
  if (raw <= 500) {
    Serial.println(F("TIMERTEST NO_OUTPUT"));
    stopTimer();
    return;
  }
  unsigned long lastReport = millis();
  while (millis() - t0 < 30000UL) {
    int v = analogRead(TIMER_OUTPUT);
    if (v <= 500) {
      Serial.print(F("TIMERTEST WINDOW_MS="));
      Serial.println(millis() - t0);
      stopTimer();
      return;
    }
    if (millis() - lastReport >= 1000) {
      lastReport = millis();
      Serial.print(F("TIMERTEST A2="));
      Serial.println(v);
    }
  }
  Serial.println(F("TIMERTEST NEVER_OFF"));
  stopTimer();
}

// ---------- keypad ----------
void sendTyped() {
  Serial.print(F("TYPED "));
  Serial.println(typed);
}

void handleKey(char key) {
  if (state != ST_ENTERING) return;
  if (key >= '0' && key <= '9') {
    if (typedCount < CODE_DIGITS) {
      typed[typedCount++] = key;
      typed[typedCount] = '\0';
      sendTyped();
    }
  } else if (key == '*') {          // clear
    typedCount = 0;
    typed[0] = '\0';
    sendTyped();
  } else if (key == '#') {          // submit, only with all digits in
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
  showCodeScreen();
}

// ---------- serial ----------
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
  } else if (strcmp(cmd, "TIMERTEST") == 0) {
    timerTest();
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

// ---------- setup and loop ----------
void setup() {
  Serial.begin(9600);
  pinMode(RED_LED, OUTPUT);
  pinMode(YELLOW_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(TIMER_TRIGGER, OUTPUT);
  digitalWrite(TIMER_TRIGGER, HIGH);  // pin 2 idles HIGH
  pinMode(TIMER_RESET, OUTPUT);
  pinMode(TIMER_OUTPUT, INPUT);
#if USE_OLED
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);  // some modules use 0x3D
#endif
  goIdle();
}

void loop() {
  readSerial();

  char key = keypad.getKey();
  if (key != NO_KEY) handleKey(key);

  if (state == ST_ENTERING) {
    unsigned long elapsed = millis() - timerStartedAt;
    bool timerDone = elapsed > 50 && !timerOutputHigh();
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
    goIdle();  // no answer from the laptop, so no green light
  }
}
