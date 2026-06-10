// ArduinoMosfetScanner v1 — Uno R3 (ATmega328P)
// See arduino-scanner/plan.md. Firmware is deliberately dumb: it sets DACs,
// drives LOW_IO, and reports averaged raw ADC counts; all physics lives in Python.
//
// I2C is BIT-BANGED (open-drain emulation) with a shared SDA and one SCL line
// per MCP4725, so identical modules need no address straps and a 3rd DAC is
// just one more SCL pin:
//   D4 = SDA (shared)   D5 = SCL1 -> DAC_H   D6 = SCL2 -> DAC_G
//
// Serial 115200, line-based ASCII. One command per line, one reply line per command.
//   IDN?            -> ArduinoMosfetScanner v1 DACH=0x60 DACG=none VREFINT_MV=1100
//   SETH <volts>    -> set DAC_H output (0..5.000 V, VDD-compensated, clamped)
//   SETG <volts>    -> set DAC_G output
//   RAWH <code>     -> raw 12-bit code to DAC_H (bring-up/debug)
//   RAWG <code>     -> raw 12-bit code to DAC_G
//   LOWIO 0|1|Z     -> drive LOW_IO (D3) low / high / float
//   MEAS?           -> averaged counts for A0..A3 under both ADC refs, plus measured VDD:
//                      VDD_MV=5012.3 A0_1V1=.. A1_1V1=.. A2_1V1=.. A3_1V1=.. A0_5V=.. A1_5V=.. A2_5V=.. A3_5V=..
//   AVG <n>         -> samples per pin per ref (1..200, default 32)
//   CALBG <mV>      -> store true internal-bandgap voltage (900..1300, EEPROM-persisted)
//   CALBG?          -> report stored bandgap cal
//   VDD?            -> measure rail via bandgap, reply VDD_MV=....
//   SCAN?           -> probe 0x08..0x77 on both buses
//   RESCAN          -> re-detect DACs after rewiring (no replug needed)
//   PINTEST [sec]   -> automatic harness diagnostic on D4/D5/D6 (see below)
//   SAVEZERO        -> write 0 into the MCP4725 EEPROMs so cold power-up is 0 V
//                      (they ship with mid-scale 2.5 V!)

#include <EEPROM.h>

const uint8_t PIN_LOWIO = 3;
const uint8_t PIN_SDA   = 4;
const uint8_t PIN_SCL1  = 5;  // bus 1 -> DAC_H
const uint8_t PIN_SCL2  = 6;  // bus 2 -> DAC_G

// ADMUX reference selections
const uint8_t REF_AVCC = _BV(REFS0);               // DEFAULT, AVcc (~5 V)
const uint8_t REF_INT  = _BV(REFS1) | _BV(REFS0);  // INTERNAL 1.1 V bandgap
const uint8_t CH_BANDGAP = 0x0E;                   // 1.1 V bandgap as ADC input

// Bandgap cal, persisted in MCU EEPROM
struct CalData { uint16_t magic; uint16_t vrefIntMv; };
const uint16_t CAL_MAGIC = 0x4D53;  // "MS"
const int CAL_ADDR = 0;

uint8_t  dacAddrH = 0, dacAddrG = 0;  // 0 = not found on that bus
uint16_t avgN = 32;
uint16_t vrefIntMv = 1100;
float    vddMv = 5000.0;

char lineBuf[40];
uint8_t lineLen = 0;

// ---------- bit-banged open-drain I2C ----------
// Lines are never driven high: LOW = OUTPUT-low, HIGH = released to pullups
// (module's onboard pullups + AVR internal). Glitch-free transitions: PORT
// bit is cleared before switching to OUTPUT, set after switching to INPUT.

const uint8_t I2C_DLY = 4;  // us; ~30-50 kHz effective with pin-call overhead

static void odRelease(uint8_t pin) {  // release line; pullups take it high
  pinMode(pin, INPUT);
  digitalWrite(pin, HIGH);  // enable internal pullup
}

static void odLow(uint8_t pin) {  // sink the line low
  digitalWrite(pin, LOW);   // drop pullup first (no driven-high glitch)
  pinMode(pin, OUTPUT);
}

static bool sclWaitHigh(uint8_t scl) {  // honor clock stretching, bounded
  for (uint16_t i = 0; i < 1000; i++) {
    if (digitalRead(scl)) return true;
    delayMicroseconds(1);
  }
  return false;
}

void i2cIdleAll() {
  odRelease(PIN_SDA);
  odRelease(PIN_SCL1);
  odRelease(PIN_SCL2);
}

void i2cStart(uint8_t scl) {
  odRelease(PIN_SDA);
  odRelease(scl);
  sclWaitHigh(scl);
  delayMicroseconds(I2C_DLY);
  odLow(PIN_SDA);
  delayMicroseconds(I2C_DLY);
  odLow(scl);
  delayMicroseconds(I2C_DLY);
}

void i2cStop(uint8_t scl) {
  odLow(PIN_SDA);
  delayMicroseconds(I2C_DLY);
  odRelease(scl);
  sclWaitHigh(scl);
  delayMicroseconds(I2C_DLY);
  odRelease(PIN_SDA);
  delayMicroseconds(I2C_DLY);
}

bool i2cWriteByte(uint8_t scl, uint8_t b) {
  for (uint8_t i = 0; i < 8; i++) {
    if (b & 0x80) odRelease(PIN_SDA); else odLow(PIN_SDA);
    b <<= 1;
    delayMicroseconds(I2C_DLY);
    odRelease(scl);
    sclWaitHigh(scl);
    delayMicroseconds(I2C_DLY);
    odLow(scl);
    delayMicroseconds(I2C_DLY);
  }
  odRelease(PIN_SDA);  // let the slave drive ACK
  delayMicroseconds(I2C_DLY);
  odRelease(scl);
  sclWaitHigh(scl);
  delayMicroseconds(I2C_DLY);
  bool ack = (digitalRead(PIN_SDA) == LOW);
  odLow(scl);
  delayMicroseconds(I2C_DLY);
  return ack;
}

bool i2cProbe(uint8_t scl, uint8_t addr) {
  i2cStart(scl);
  bool ack = i2cWriteByte(scl, (uint8_t)(addr << 1));
  i2cStop(scl);
  return ack;
}

// ---------- MCP4725 ----------

bool dacWrite(uint8_t scl, uint8_t addr, uint16_t code) {
  if (addr == 0) return false;
  if (code > 4095) code = 4095;
  i2cStart(scl);
  bool ok = i2cWriteByte(scl, (uint8_t)(addr << 1));
  ok = i2cWriteByte(scl, (uint8_t)((code >> 8) & 0x0F)) && ok;  // fast mode, PD=00
  ok = i2cWriteByte(scl, (uint8_t)(code & 0xFF)) && ok;
  i2cStop(scl);
  return ok;
}

bool dacWriteEeprom(uint8_t scl, uint8_t addr, uint16_t code) {
  if (addr == 0) return false;
  i2cStart(scl);
  bool ok = i2cWriteByte(scl, (uint8_t)(addr << 1));
  ok = i2cWriteByte(scl, 0x60) && ok;  // write DAC register + EEPROM
  ok = i2cWriteByte(scl, (uint8_t)(code >> 4)) && ok;
  ok = i2cWriteByte(scl, (uint8_t)((code & 0x0F) << 4)) && ok;
  i2cStop(scl);
  delay(60);  // EEPROM write time (max 50 ms)
  return ok;
}

uint8_t scanBusForDac(uint8_t scl) {
  // MCP4725 base address depends on chip variant: A0=0x60/61, A1=0x62/63,
  // A2=0x64/65, A3=0x66/67 — take the first that ACKs on this bus.
  for (uint8_t addr = 0x60; addr <= 0x67; addr++) {
    if (i2cProbe(scl, addr)) return addr;
  }
  return 0;
}

void scanDacs() {
  i2cIdleAll();
  dacAddrH = scanBusForDac(PIN_SCL1);
  dacAddrG = scanBusForDac(PIN_SCL2);
}

// ---------- ADC ----------

uint16_t adcOnce(uint8_t admux) {
  ADMUX = admux;
  ADCSRA |= _BV(ADSC);
  while (ADCSRA & _BV(ADSC)) ;
  return ADCW;
}

// Switch reference and let AREF (100 nF on the Uno) settle. The bandgap drives
// AREF through ~32 k, so INTERNAL needs real time; AVcc is low-impedance.
void settleRef(uint8_t refBits) {
  adcOnce(refBits | 0);
  adcOnce(refBits | 0);
  delay(refBits == REF_INT ? 10 : 3);
  adcOnce(refBits | 0);
}

float readAvg(uint8_t refBits, uint8_t channel) {
  adcOnce(refBits | channel);  // discard after mux change
  adcOnce(refBits | channel);
  uint32_t sum = 0;
  for (uint16_t i = 0; i < avgN; i++) sum += adcOnce(refBits | channel);
  return (float)sum / (float)avgN;
}

// Read the 1.1 V bandgap against AVcc to compute the actual rail.
float measureVddMv() {
  adcOnce(REF_AVCC | CH_BANDGAP);  // bandgap needs ~1 ms to stabilise as an input
  delay(2);
  for (uint8_t i = 0; i < 4; i++) adcOnce(REF_AVCC | CH_BANDGAP);
  uint32_t sum = 0;
  for (uint8_t i = 0; i < 16; i++) sum += adcOnce(REF_AVCC | CH_BANDGAP);
  float counts = (float)sum / 16.0;
  if (counts < 1.0) counts = 1.0;
  return (float)vrefIntMv * 1024.0 / counts;
}

// ---------- commands ----------

void printDacField(const __FlashStringHelper* key, uint8_t addr) {
  Serial.print(key);
  if (addr != 0) { Serial.print(F("0x")); Serial.print(addr, HEX); }
  else           Serial.print(F("none"));
}

void printBanner() {
  Serial.print(F("ArduinoMosfetScanner v1 "));
  printDacField(F("DACH="), dacAddrH);
  printDacField(F(" DACG="), dacAddrG);
  Serial.print(F(" VREFINT_MV=")); Serial.println(vrefIntMv);
}

void cmdSetVolts(uint8_t scl, uint8_t addr, const char* arg) {
  if (addr == 0) { Serial.println(F("ERR no DAC")); return; }
  if (arg == NULL) { Serial.println(F("ERR missing volts")); return; }
  float v = atof(arg);
  if (v < 0) v = 0;
  if (v > 5.0) v = 5.0;
  long code = (long)(v * 1000.0 * 4096.0 / vddMv + 0.5);
  if (code > 4095) code = 4095;
  if (!dacWrite(scl, addr, (uint16_t)code)) { Serial.println(F("ERR i2c nack")); return; }
  Serial.print(F("OK CODE=")); Serial.print(code);
  Serial.print(F(" VDD_MV=")); Serial.println(vddMv, 1);
}

void cmdRaw(uint8_t scl, uint8_t addr, const char* arg) {
  if (addr == 0) { Serial.println(F("ERR no DAC")); return; }
  if (arg == NULL) { Serial.println(F("ERR missing code")); return; }
  long code = atol(arg);
  if (code < 0 || code > 4095) { Serial.println(F("ERR code 0..4095")); return; }
  if (!dacWrite(scl, addr, (uint16_t)code)) { Serial.println(F("ERR i2c nack")); return; }
  Serial.println(F("OK"));
}

void cmdLowio(const char* arg) {
  if (arg == NULL) { Serial.println(F("ERR missing arg")); return; }
  if (arg[0] == '0') { pinMode(PIN_LOWIO, OUTPUT); digitalWrite(PIN_LOWIO, LOW); }
  else if (arg[0] == '1') { pinMode(PIN_LOWIO, OUTPUT); digitalWrite(PIN_LOWIO, HIGH); }
  else if (arg[0] == 'Z' || arg[0] == 'z') { pinMode(PIN_LOWIO, INPUT); }
  else { Serial.println(F("ERR arg 0|1|Z")); return; }
  Serial.println(F("OK"));
}

void printPair(const __FlashStringHelper* key, float val) {
  Serial.print(key); Serial.print(val, 2);
}

void cmdMeas() {
  settleRef(REF_AVCC);
  vddMv = measureVddMv();
  float a5[4], a1[4];
  for (uint8_t ch = 0; ch < 4; ch++) a5[ch] = readAvg(REF_AVCC, ch);
  settleRef(REF_INT);
  for (uint8_t ch = 0; ch < 4; ch++) a1[ch] = readAvg(REF_INT, ch);
  settleRef(REF_AVCC);  // leave in a known state

  Serial.print(F("VDD_MV=")); Serial.print(vddMv, 1);
  printPair(F(" A0_1V1="), a1[0]);
  printPair(F(" A1_1V1="), a1[1]);
  printPair(F(" A2_1V1="), a1[2]);
  printPair(F(" A3_1V1="), a1[3]);
  printPair(F(" A0_5V="), a5[0]);
  printPair(F(" A1_5V="), a5[1]);
  printPair(F(" A2_5V="), a5[2]);
  printPair(F(" A3_5V="), a5[3]);
  Serial.println();
}

void cmdCalbg(const char* arg) {
  if (arg == NULL) { Serial.println(F("ERR missing mV")); return; }
  long mv = atol(arg);
  if (mv < 900 || mv > 1300) { Serial.println(F("ERR 900..1300")); return; }
  vrefIntMv = (uint16_t)mv;
  CalData cal = { CAL_MAGIC, vrefIntMv };
  EEPROM.put(CAL_ADDR, cal);
  settleRef(REF_AVCC);
  vddMv = measureVddMv();
  Serial.print(F("OK CALBG_MV=")); Serial.print(vrefIntMv);
  Serial.print(F(" VDD_MV=")); Serial.println(vddMv, 1);
}

void cmdScanBus(const __FlashStringHelper* label, uint8_t scl) {
  Serial.print(label);
  bool any = false;
  for (uint8_t a = 0x08; a <= 0x77; a++) {
    if (i2cProbe(scl, a)) {
      if (any) Serial.print(',');
      Serial.print(F("0x")); Serial.print(a, HEX);
      any = true;
    }
  }
  if (!any) Serial.print(F("none"));
}

void cmdPintest(const char* arg) {
  // Automatic harness diagnostic on SDA(D4)/SCL1(D5)/SCL2(D6). Per line:
  //   IDLE   - level as a floating input
  //   EXTPU  - discharge node, release, read 20 us later: HIGH => an external
  //            pullup (a powered module) is really attached to this wire
  //   5VSHORT- drive LOW 1 ms, read back: still HIGH => pin is fighting a
  //            hard 5 V source (mis-plugged into a power pin)
  //   BRIDGED- driving one line low dragged another (idle-high) line low
  // Optional `PINTEST <seconds>`: afterwards hold all three LOW for a
  // voltmeter check at the module - only if no short was detected.
  const uint8_t pins[3] = { PIN_SDA, PIN_SCL1, PIN_SCL2 };
  uint8_t idle[3], extpu[3], shorted[3];
  uint8_t bridged = 0;

  long sec = (arg != NULL) ? atol(arg) : 0;
  if (sec > 120) sec = 120;

  for (uint8_t i = 0; i < 3; i++) {  // all floating, no pullups
    pinMode(pins[i], INPUT);
    digitalWrite(pins[i], LOW);
  }
  delay(2);
  for (uint8_t i = 0; i < 3; i++) idle[i] = digitalRead(pins[i]);

  for (uint8_t i = 0; i < 3; i++) {
    odLow(pins[i]);
    delay(1);
    shorted[i] = digitalRead(pins[i]);  // 1 = tied to 5 V
    for (uint8_t j = 0; j < 3; j++) {
      if (j != i && idle[j] == 1 && digitalRead(pins[j]) == 0) bridged = 1;
    }
    pinMode(pins[i], INPUT);  // release, no pullup
    delayMicroseconds(20);
    extpu[i] = digitalRead(pins[i]);  // 1 = external pullup recharged the node
  }

  bool safe = !shorted[0] && !shorted[1] && !shorted[2];
  if (sec > 0 && safe) {  // voltmeter window
    for (uint8_t i = 0; i < 3; i++) odLow(pins[i]);
    delay((unsigned long)sec * 1000UL);
  }

  i2cIdleAll();
  scanDacs();
  dacWrite(PIN_SCL1, dacAddrH, 0);
  dacWrite(PIN_SCL2, dacAddrG, 0);

  Serial.print(F("OK PINTEST"));
  const __FlashStringHelper* names[3] = { F(" SDA"), F(" SCL1"), F(" SCL2") };
  for (uint8_t i = 0; i < 3; i++) {
    Serial.print(names[i]);
    Serial.print(F("_IDLE=")); Serial.print(idle[i]);
    Serial.print(names[i]);
    Serial.print(F("_EXTPU=")); Serial.print(extpu[i]);
    Serial.print(names[i]);
    Serial.print(F("_5VSHORT=")); Serial.print(shorted[i]);
  }
  Serial.print(F(" BRIDGED=")); Serial.print(bridged);
  if (sec > 0 && !safe) Serial.print(F(" HOLD=SKIPPED-UNSAFE"));
  Serial.print(F(" "));
  printBanner();
}

void handleLine(char* line) {
  // uppercase in place
  for (char* p = line; *p; p++) *p = toupper(*p);
  char* cmd = strtok(line, " \t");
  if (cmd == NULL) return;
  char* arg = strtok(NULL, " \t");

  if      (strcmp(cmd, "IDN?") == 0)     printBanner();
  else if (strcmp(cmd, "SETH") == 0)     cmdSetVolts(PIN_SCL1, dacAddrH, arg);
  else if (strcmp(cmd, "SETG") == 0)     cmdSetVolts(PIN_SCL2, dacAddrG, arg);
  else if (strcmp(cmd, "RAWH") == 0)     cmdRaw(PIN_SCL1, dacAddrH, arg);
  else if (strcmp(cmd, "RAWG") == 0)     cmdRaw(PIN_SCL2, dacAddrG, arg);
  else if (strcmp(cmd, "LOWIO") == 0)    cmdLowio(arg);
  else if (strcmp(cmd, "MEAS?") == 0)    cmdMeas();
  else if (strcmp(cmd, "VDD?") == 0) {
    settleRef(REF_AVCC);
    vddMv = measureVddMv();
    Serial.print(F("VDD_MV=")); Serial.println(vddMv, 1);
  }
  else if (strcmp(cmd, "AVG") == 0) {
    long n = (arg != NULL) ? atol(arg) : 0;
    if (n < 1 || n > 200) { Serial.println(F("ERR 1..200")); return; }
    avgN = (uint16_t)n;
    Serial.println(F("OK"));
  }
  else if (strcmp(cmd, "CALBG") == 0)    cmdCalbg(arg);
  else if (strcmp(cmd, "CALBG?") == 0) {
    Serial.print(F("CALBG_MV=")); Serial.println(vrefIntMv);
  }
  else if (strcmp(cmd, "SCAN?") == 0) {
    cmdScanBus(F("B1(SDA4/SCL5)="), PIN_SCL1);
    cmdScanBus(F(" B2(SDA4/SCL6)="), PIN_SCL2);
    Serial.println();
  }
  else if (strcmp(cmd, "RESCAN") == 0) {  // re-detect DACs after rewiring
    scanDacs();
    dacWrite(PIN_SCL1, dacAddrH, 0);
    dacWrite(PIN_SCL2, dacAddrG, 0);
    printBanner();
  }
  else if (strcmp(cmd, "PINTEST") == 0)  cmdPintest(arg);
  else if (strcmp(cmd, "SAVEZERO") == 0) {
    if (dacAddrH == 0 && dacAddrG == 0) { Serial.println(F("ERR no DAC")); return; }
    bool ok = true;
    if (dacAddrH != 0) ok = dacWriteEeprom(PIN_SCL1, dacAddrH, 0) && ok;
    if (dacAddrG != 0) ok = dacWriteEeprom(PIN_SCL2, dacAddrG, 0) && ok;
    Serial.println(ok ? F("OK") : F("ERR i2c nack"));
  }
  else {
    Serial.print(F("ERR unknown cmd '"));
    Serial.print(cmd);
    Serial.print(F("' hex="));
    for (char* p = cmd; *p; p++) { Serial.print((uint8_t)*p, HEX); Serial.print(' '); }
    Serial.println();
  }
}

// ---------- setup / loop ----------

void setup() {
  pinMode(PIN_LOWIO, OUTPUT);
  digitalWrite(PIN_LOWIO, LOW);

  Serial.begin(115200);
  i2cIdleAll();

  // ADC: enable, prescaler 128 -> 125 kHz (the core does this too; be explicit)
  ADCSRA = _BV(ADEN) | _BV(ADPS2) | _BV(ADPS1) | _BV(ADPS0);

  CalData cal;
  EEPROM.get(CAL_ADDR, cal);
  if (cal.magic == CAL_MAGIC && cal.vrefIntMv >= 900 && cal.vrefIntMv <= 1300)
    vrefIntMv = cal.vrefIntMv;

  scanDacs();
  dacWrite(PIN_SCL1, dacAddrH, 0);  // DACs hold last value through a reset; force 0 V
  dacWrite(PIN_SCL2, dacAddrG, 0);

  settleRef(REF_AVCC);
  vddMv = measureVddMv();

  printBanner();
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        lineBuf[lineLen] = '\0';
        handleLine(lineBuf);
        lineLen = 0;
      }
    } else if (c >= 32 && c < 127 && lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;  // ASCII protocol: drop line noise / USB garbage
    }
  }
}
