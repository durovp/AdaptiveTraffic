#include "TrafficProtocol.h"

// D0/D1 belong to Serial; D13 is deliberately unused.
const uint8_t LED_PINS[14] = {2,3,4,5,6,7,8,9,10,11,12,A0,A1,A2};
const uint8_t BUTTON_PIN = A3;
uint16_t displayed = 0xFFFF;
void writeLamps(uint16_t bits) {
  if (bits==displayed) return;
  // Break before make, even inside the few microseconds of GPIO updates.
  for (uint8_t i=0;i<14;++i) digitalWrite(LED_PINS[i],LOW);
  for (uint8_t i=0;i<14;++i) if (bits & (uint16_t(1)<<i)) digitalWrite(LED_PINS[i],HIGH);
  displayed=bits;
}
void sendLine(const char* line);
traffic::Protocol protocol(sendLine);
void sendLine(const char* line) {
  // Apply the physical state BEFORE confirming it, including faults.
  writeLamps(traffic::lamps(protocol.phase));
  Serial.println(line);
}
void setup() {
  for (uint8_t i=0;i<14;++i) { digitalWrite(LED_PINS[i],LOW); pinMode(LED_PINS[i],OUTPUT); }
  pinMode(BUTTON_PIN,INPUT_PULLUP);
  protocol.begin(millis(),digitalRead(BUTTON_PIN)==LOW);
  writeLamps(traffic::lamps(protocol.phase));
  Serial.begin(115200);
}
void loop() {
  protocol.poll(millis(),digitalRead(BUTTON_PIN)==LOW);
  // Bound processing so continuous garbage cannot starve the watchdog/button.
  for (uint8_t i=0;i<32 && Serial.available();++i) protocol.byte(char(Serial.read()),millis());
  writeLamps(traffic::lamps(protocol.phase));
}
