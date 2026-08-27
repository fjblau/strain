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

const int CH0_BASE = 10;                       // 5 kg cell
const int CH3_BASE = 20;                       // 300 g cell

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

void setup() {
  Serial.begin(115200);
  while (!Serial);

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
  Serial.println("%");

  delay(200);
}
