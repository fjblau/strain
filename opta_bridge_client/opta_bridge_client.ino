#include <WiFi.h>
#include <ArduinoModbus.h>

// PhidgetBridge 1046 -> Pi Modbus gateway (bridge_modbus.py)
// Register map (zero-based holding registers, FC 3):
//   0  heartbeat (uint16, increments each update; watchdog)
//   1  status word (bit0 cal_loaded, bit1 any_over, bit2 any_warn, bit3 all_attached)
//   2  channel count
//   3  update interval (ms)
// Per channel, base = 10 + index*10:
//   +0 phidget channel number
//   +1 state (0 ok,1 warn,2 over | bit8 calibrated, bit9 valid)
//   +2..3 weight float32 (high word first, ABCD)
//   +4 weight int16 (rounded, signed)
//   +5..6 capacity float32
//   +7 percent of capacity (tenths, 0-1000 = 0-100.0%)
// Coils (FC 5): 0+index = tare channel, 100+index = clear tare (auto-cleared).

char ssid[] = "FRITZ!Box 6670 KX 24";                   // <-- your 2.4 GHz WiFi name
char pass[] = "03744633400933002383";             // <-- your WiFi password

IPAddress piIP(192, 168, 178, 50);             // the Pi
const uint16_t PI_PORT = 1502;                 // use 1502 for the non-privileged test server

const int CH0_BASE = 10;                       // 5 kg cell (fill channel)
const int CH3_BASE = 20;                       // 300 g cell

// Fill control block (see bridge_modbus.py):
const int HR_TARGET  = 101;                    // target weight float32 (101/102)
const int HR_STATE   = 110;                    // fill state (we write it)
const int HR_FINAL   = 111;                    // final weight float32 (111/112)
const int COIL_START = 200;                    // START (HMI writes, we clear)
const int COIL_ABORT = 201;                    // ABORT (HMI writes, we clear)

const uint8_t FILL_IDLE = 0, FILL_COARSE = 1, FILL_DRIBBLE = 2,
              FILL_DONE = 3, FILL_FAULT = 4;

const float DRIBBLE_FRAC = 0.85;               // coarse -> dribble at 85% of target
const unsigned long COMM_TIMEOUT_MS = 2000;    // heartbeat watchdog

const int COARSE_PIN  = D0;                    // coarse fill valve (relay out)
const int DRIBBLE_PIN = D1;                    // dribble (slow) fill valve

uint8_t       fillState = FILL_IDLE;
uint16_t      lastHb = 0;
unsigned long lastHbMs = 0;

WiFiClient wifi;
ModbusTCPClient modbus(wifi);

float readWeightF(int base) {                  // +2/+3 float32, high word first (ABCD)
  uint16_t hi = modbus.holdingRegisterRead(base + 2);
  uint16_t lo = modbus.holdingRegisterRead(base + 3);
  uint32_t raw = ((uint32_t)hi << 16) | lo;
  float f;
  memcpy(&f, &raw, 4);
  return f;
}

float readFloatAt(int base) {                  // float32 at base/base+1 (ABCD)
  uint16_t hi = modbus.holdingRegisterRead(base);
  uint16_t lo = modbus.holdingRegisterRead(base + 1);
  uint32_t raw = ((uint32_t)hi << 16) | lo;
  float f;
  memcpy(&f, &raw, 4);
  return f;
}

void writeFloatAt(int base, float f) {
  uint32_t raw;
  memcpy(&raw, &f, 4);
  modbus.holdingRegisterWrite(base, (uint16_t)(raw >> 16));
  modbus.holdingRegisterWrite(base + 1, (uint16_t)(raw & 0xFFFF));
}

void setValves(bool coarse, bool dribble) {
  digitalWrite(COARSE_PIN, coarse ? HIGH : LOW);
  digitalWrite(DRIBBLE_PIN, dribble ? HIGH : LOW);
  digitalWrite(LED_D0, coarse ? HIGH : LOW);
  digitalWrite(LED_D1, dribble ? HIGH : LOW);
}

void setFillState(uint8_t s) {
  fillState = s;
  modbus.holdingRegisterWrite(HR_STATE, s);
}

void setup() {
  Serial.begin(115200);
  while (!Serial);

  pinMode(COARSE_PIN, OUTPUT);
  pinMode(DRIBBLE_PIN, OUTPUT);
  pinMode(LED_D0, OUTPUT);
  pinMode(LED_D1, OUTPUT);
  setValves(false, false);

  Serial.print("Connecting to WiFi \"");
  Serial.print(ssid);
  Serial.println("\" (2.4 GHz)...");

  int tries = 0;
  while (WiFi.begin(ssid, pass) != WL_CONNECTED) {
    Serial.print(".");
    delay(1000);
    if (++tries >= 20) {
      Serial.println(" still trying (check SSID/password, 2.4 GHz band)");
      tries = 0;
    }
  }

  Serial.println(" connected");
  Serial.print("Opta IP: "); Serial.println(WiFi.localIP());
  Serial.print("Gateway: "); Serial.println(WiFi.gatewayIP());
  Serial.print("RSSI   : "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
  Serial.print("Target : "); Serial.print(piIP);
  Serial.print(":"); Serial.println(PI_PORT);
}

void loop() {
  if (!modbus.connected()) {
    Serial.print("Connecting to Pi... ");
    if (!modbus.begin(piIP, PI_PORT)) {
      Serial.println("failed");
      delay(1000);
      return;
    }
    Serial.println("ok");
  }

  uint16_t hb      = modbus.holdingRegisterRead(0);
  uint16_t status  = modbus.holdingRegisterRead(1);
  int16_t  ch0_i   = (int16_t)modbus.holdingRegisterRead(CH0_BASE + 4);
  float    ch0_f   = readWeightF(CH0_BASE);
  float    ch3_f   = readWeightF(CH3_BASE);
  uint16_t ch3_pct = modbus.holdingRegisterRead(CH3_BASE + 7);

  if (hb != lastHb) { lastHb = hb; lastHbMs = millis(); }
  bool commLost = (millis() - lastHbMs) > COMM_TIMEOUT_MS;
  bool over     = (status & 0x0002) != 0;      // any_over interlock

  int startRead = modbus.coilRead(COIL_START);
  bool startCmd = (startRead > 0);
  if (startCmd) modbus.coilWrite(COIL_START, 0);
  int abortRead = modbus.coilRead(COIL_ABORT);
  bool abortCmd = (abortRead > 0);
  if (abortCmd) modbus.coilWrite(COIL_ABORT, 0);

  float target = readFloatAt(HR_TARGET);

  if (over || commLost) {
    setValves(false, false);
    if (fillState != FILL_FAULT) setFillState(FILL_FAULT);
  } else if (abortCmd) {
    setValves(false, false);
    setFillState(FILL_IDLE);
  } else {
    switch (fillState) {
      case FILL_IDLE:
      case FILL_DONE:
      case FILL_FAULT:
        setValves(false, false);
        if (startCmd && target > 0.0) setFillState(FILL_COARSE);
        break;
      case FILL_COARSE:
        setValves(true, false);
        if (ch0_f >= target * DRIBBLE_FRAC) setFillState(FILL_DRIBBLE);
        break;
      case FILL_DRIBBLE:
        setValves(false, true);
        if (ch0_f >= target) {
          setValves(false, false);
          writeFloatAt(HR_FINAL, ch0_f);
          setFillState(FILL_DONE);
        }
        break;
    }
  }

  const char *sname = "?";
  switch (fillState) {
    case FILL_IDLE:    sname = "IDLE";    break;
    case FILL_COARSE:  sname = "COARSE";  break;
    case FILL_DRIBBLE: sname = "DRIBBLE"; break;
    case FILL_DONE:    sname = "DONE";    break;
    case FILL_FAULT:   sname = "FAULT";   break;
  }

  Serial.print("hb=");
  Serial.print(hb);
  Serial.print(" status=0x");
  Serial.print(status, HEX);
  Serial.print(" | CH0=");
  Serial.print(ch0_f, 1);
  Serial.print("g (int ");
  Serial.print(ch0_i);
  Serial.print(")");
  Serial.print(" | CH3=");
  Serial.print(ch3_f, 1);
  Serial.print("g ");
  Serial.print(ch3_pct / 10.0, 1);
  Serial.print("% | fill=");
  Serial.print(sname);
  Serial.print(" target=");
  Serial.print(target, 1);
  Serial.println("g");

  delay(200);
}
