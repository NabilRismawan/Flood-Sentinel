#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>

/*
  FloodSentinel - Milestone 3: Water-Level Trend & Adaptive Warning
  + MQTT telemetry ke HiveMQ
  Board: ESP32 DevKit V1
  Sensor: HC-SR04

  SIMULATION PARAMETERS ONLY:
  - Reference height: 100 cm
  - NORMAL: water level < 30 cm
  - SIAGA: 30-60 cm
  - BAHAYA: > 60 cm
  - Rapid-rise threshold: >= 5 cm/min

  These values are for Wokwi logic testing only. They are NOT validated
  flood-safety thresholds and must not be used as real-world warning limits.

  MQTT topics (TOPIC_BASE = floodsentinel/13223072):
  - <base>/telemetry : JSON lengkap, tiap 2 detik + langsung saat status berubah
  - <base>/status    : teks final status (retained)
  - <base>/online    : "online" / "offline" (retained, offline via Last Will)
  - <base>/cmd       : perintah masuk -> "buzzer off" | "buzzer on"
*/

// ===================== WiFi & MQTT =====================
// 0 = HiveMQ Public Broker (broker.hivemq.com:1883, tanpa login)
// 1 = HiveMQ Cloud (TLS :8883). Isi secrets.h dengan:
//     #define MQTT_HOST "xxxxxxxx.s1.eu.hivemq.cloud"
//     #define MQTT_USER "username"
//     #define MQTT_PASS "password"
#define USE_HIVEMQ_CLOUD 0

#if USE_HIVEMQ_CLOUD
  #include <WiFiClientSecure.h>
  #include "secrets.h"
  WiFiClientSecure netClient;
  const char* mqttHost = MQTT_HOST;
  const uint16_t mqttPort = 8883;
  const char* mqttUser = MQTT_USER;
  const char* mqttPass = MQTT_PASS;
#else
  WiFiClient netClient;
  const char* mqttHost = "broker.hivemq.com";
  const uint16_t mqttPort = 1883;
  const char* mqttUser = nullptr;
  const char* mqttPass = nullptr;
#endif

PubSubClient mqtt(netClient);

const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";

#define TOPIC_BASE      "floodsentinel/13223072"
#define TOPIC_TELEMETRY TOPIC_BASE "/telemetry"
#define TOPIC_STATUS    TOPIC_BASE "/status"
#define TOPIC_ONLINE    TOPIC_BASE "/online"
#define TOPIC_CMD       TOPIC_BASE "/cmd"

const unsigned long PUBLISH_INTERVAL_MS = 2000UL;
const unsigned long MQTT_RETRY_MS = 5000UL;
const unsigned long WIFI_TIMEOUT_MS = 15000UL;

unsigned long lastPublish = 0;
unsigned long lastMqttAttempt = 0;
int lastPublishedStatus = -1;
bool buzzerEnabled = true;

// ===================== Hardware =====================
const int TRIG_PIN = 23;
const int ECHO_PIN = 18;
const int LED_GREEN = 25;
const int LED_YELLOW = 26;
const int LED_RED = 27;
const int BUZZER_PIN = 14;

const float REFERENCE_HEIGHT_CM = 100.0;
const float ALERT_THRESHOLD_CM = 30.0;
const float DANGER_THRESHOLD_CM = 60.0;

// A rapid rise raises the warning by one level.
// Simulation threshold only; not a field-validated value.
const float RAPID_RISE_THRESHOLD_CM_MIN = 5.0;

const unsigned long ECHO_TIMEOUT_US = 35000UL;
const unsigned long SENSOR_INTERVAL_MS = 500UL;
const unsigned long ERROR_BEEP_INTERVAL_MS = 2000UL;

// Rate-of-rise history: retain approximately 15 seconds of samples.
const int HISTORY_SIZE = 30;
const unsigned long RATE_WINDOW_MS = 10000UL;
const unsigned long MIN_RATE_WINDOW_MS = 8000UL;

unsigned long lastSensorRead = 0;
unsigned long lastErrorBeep = 0;
bool errorBeepOn = false;

float levelHistory[HISTORY_SIZE];
unsigned long timeHistory[HISTORY_SIZE];
int historyCount = 0;
int historyWriteIndex = 0;

typedef enum {
  NORMAL,
  SIAGA,
  BAHAYA,
  SENSOR_ERROR
} WaterStatus;

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (duration == 0) return -1.0;

  float distance = duration / 58.0;

  // Accept a small boundary tolerance, then clamp to HC-SR04's nominal range.
  if (distance < 2.0 || distance > 405.0) return -1.0;
  if (distance > 400.0) distance = 400.0;

  return distance;
}

// Median filter reduces occasional unstable readings.
float readFilteredDistance() {
  const int SAMPLE_COUNT = 5;
  float samples[SAMPLE_COUNT];
  int validCount = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    float value = readDistanceCm();
    if (value >= 0.0) {
      samples[validCount++] = value;
    }
    delay(30);
  }

  if (validCount < 3) return -1.0;

  // Insertion sort; array contains at most five values.
  for (int i = 1; i < validCount; i++) {
    float key = samples[i];
    int j = i - 1;

    while (j >= 0 && samples[j] > key) {
      samples[j + 1] = samples[j];
      j--;
    }

    samples[j + 1] = key;
  }

  return samples[validCount / 2];
}

void addLevelHistory(float waterLevel, unsigned long timestamp) {
  levelHistory[historyWriteIndex] = waterLevel;
  timeHistory[historyWriteIndex] = timestamp;

  historyWriteIndex = (historyWriteIndex + 1) % HISTORY_SIZE;

  if (historyCount < HISTORY_SIZE) {
    historyCount++;
  }
}

// Calculate rate using the stored sample closest to a 10-second-old sample.
// Returns true only after enough history has accumulated.
bool calculateRiseRate(float currentLevel,
                       unsigned long currentTime,
                       float &riseRateCmMin) {
  if (historyCount < 2) return false;

  bool found = false;
  unsigned long bestAgeDifference = 0xFFFFFFFFUL;
  unsigned long selectedElapsed = 0;
  float selectedLevel = 0.0;

  for (int i = 0; i < historyCount; i++) {
    unsigned long sampleTime = timeHistory[i];
    unsigned long age = currentTime - sampleTime;

    if (age < MIN_RATE_WINDOW_MS) continue;

    unsigned long difference =
      (age > RATE_WINDOW_MS) ? (age - RATE_WINDOW_MS)
                             : (RATE_WINDOW_MS - age);

    if (difference < bestAgeDifference) {
      bestAgeDifference = difference;
      selectedElapsed = age;
      selectedLevel = levelHistory[i];
      found = true;
    }
  }

  if (!found || selectedElapsed == 0) return false;

  float changeCm = currentLevel - selectedLevel;
  riseRateCmMin = changeCm * 60000.0 / (float)selectedElapsed;

  return true;
}

void allIndicatorsOff() {
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_YELLOW, LOW);
  digitalWrite(LED_RED, LOW);
  noTone(BUZZER_PIN);
}

WaterStatus classifyWaterLevel(float waterLevel) {
  if (waterLevel < ALERT_THRESHOLD_CM) return NORMAL;
  if (waterLevel <= DANGER_THRESHOLD_CM) return SIAGA;
  return BAHAYA;
}

// Rapid rise escalates the base status by one level.
// It never reduces a status determined by water height.
WaterStatus applyAdaptiveWarning(WaterStatus baseStatus,
                                 float riseRate,
                                 bool rateIsValid) {
  if (!rateIsValid || riseRate < RAPID_RISE_THRESHOLD_CM_MIN) {
    return baseStatus;
  }

  if (baseStatus == NORMAL) return SIAGA;
  if (baseStatus == SIAGA) return BAHAYA;

  return BAHAYA;
}

const char* statusToText(WaterStatus status) {
  switch (status) {
    case NORMAL: return "NORMAL";
    case SIAGA: return "SIAGA";
    case BAHAYA: return "BAHAYA";
    case SENSOR_ERROR: return "SENSOR_ERROR";
    default: return "UNKNOWN";
  }
}

void showStatus(WaterStatus status) {
  allIndicatorsOff();

  switch (status) {
    case NORMAL:
      digitalWrite(LED_GREEN, HIGH);
      break;

    case SIAGA:
      digitalWrite(LED_YELLOW, HIGH);
      break;

    case BAHAYA:
      digitalWrite(LED_RED, HIGH);
      if (buzzerEnabled) tone(BUZZER_PIN, 2000);
      break;

    case SENSOR_ERROR:
      // Error indication: red LED blinks and buzzer chirps periodically.
      digitalWrite(LED_RED, (millis() / 300) % 2);

      if (millis() - lastErrorBeep >= ERROR_BEEP_INTERVAL_MS) {
        lastErrorBeep = millis();
        errorBeepOn = !errorBeepOn;

        if (errorBeepOn && buzzerEnabled) {
          tone(BUZZER_PIN, 1200, 180);
        } else {
          noTone(BUZZER_PIN);
        }
      }
      break;
  }
}

// ===================== MQTT =====================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();
  msg.toLowerCase();

  Serial.print("MQTT <- [");
  Serial.print(topic);
  Serial.print("] ");
  Serial.println(msg);

  if (msg == "buzzer off") {
    buzzerEnabled = false;
    noTone(BUZZER_PIN);
  } else if (msg == "buzzer on") {
    buzzerEnabled = true;
  } else {
    Serial.println("MQTT: perintah tidak dikenal (pakai 'buzzer on' / 'buzzer off')");
    return;
  }

  lastPublishedStatus = -1;  // paksa publish berikutnya agar buzzer_enabled terbarui
}

void connectWiFi() {
  Serial.print("WiFi: connecting");
  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);  // channel 6 mempercepat koneksi di Wokwi

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(" OK, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(" GAGAL (monitoring lokal tetap berjalan)");
  }
}

// Non-blocking reconnect: monitoring banjir tetap jalan walau broker putus.
void maintainMqtt() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqtt.connected()) {
    if (lastMqttAttempt != 0 && millis() - lastMqttAttempt < MQTT_RETRY_MS) return;
    lastMqttAttempt = millis();

    String clientId = "fs13223072-" + String(random(0xffff), HEX);
    Serial.print("MQTT: connect ke ");
    Serial.print(mqttHost);
    Serial.print(":");
    Serial.print(mqttPort);
    Serial.print(" ... ");

    // Last Will: broker otomatis publish "offline" kalau ESP32 terputus.
    if (mqtt.connect(clientId.c_str(), mqttUser, mqttPass,
                     TOPIC_ONLINE, 0, true, "offline")) {
      Serial.println("OK");
      mqtt.publish(TOPIC_ONLINE, "online", true);
      mqtt.subscribe(TOPIC_CMD);
      lastPublishedStatus = -1;  // kirim data segera setelah tersambung
    } else {
      Serial.print("gagal, rc=");
      Serial.println(mqtt.state());
      return;
    }
  }

  mqtt.loop();
}

// distance < 0 berarti sensor error -> field jarak/level dikirim sebagai null.
void publishTelemetry(float distance, float waterLevel,
                      WaterStatus baseStatus, float riseRate,
                      bool rateIsValid, WaterStatus finalStatus) {
  if (!mqtt.connected()) return;

  bool statusChanged = ((int)finalStatus != lastPublishedStatus);
  if (!statusChanged && millis() - lastPublish < PUBLISH_INTERVAL_MS) return;

  char distText[12], levelText[12], rateText[12];
  if (distance >= 0.0) {
    snprintf(distText, sizeof(distText), "%.1f", distance);
    snprintf(levelText, sizeof(levelText), "%.1f", waterLevel);
  } else {
    strcpy(distText, "null");
    strcpy(levelText, "null");
  }
  if (rateIsValid) snprintf(rateText, sizeof(rateText), "%.2f", riseRate);
  else strcpy(rateText, "null");

  bool rapidRise = rateIsValid && riseRate >= RAPID_RISE_THRESHOLD_CM_MIN;

  char payload[320];
  snprintf(payload, sizeof(payload),
           "{\"distance_cm\":%s,\"water_level_cm\":%s,\"base_status\":\"%s\","
           "\"rise_rate_cm_min\":%s,\"rapid_rise\":%s,\"final_status\":\"%s\","
           "\"buzzer_enabled\":%s,\"uptime_s\":%lu}",
           distText, levelText, statusToText(baseStatus), rateText,
           rapidRise ? "true" : "false", statusToText(finalStatus),
           buzzerEnabled ? "true" : "false", millis() / 1000UL);

  mqtt.publish(TOPIC_TELEMETRY, payload);
  if (statusChanged) {
    mqtt.publish(TOPIC_STATUS, statusToText(finalStatus), true);
  }

  lastPublish = millis();
  lastPublishedStatus = (int)finalStatus;

  Serial.print("MQTT -> ");
  Serial.println(payload);
}

// ===================== Main =====================
void setup() {
  Serial.begin(115200);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  allIndicatorsOff();

  Serial.println();
  Serial.println("=== FloodSentinel Milestone 3 + MQTT ===");
  Serial.println("Water-level monitoring + rate-of-rise + adaptive warning");
  Serial.println("Reference height: 100 cm (simulation only)");
  Serial.println("Height thresholds: NORMAL <30 | SIAGA 30-60 | BAHAYA >60 cm");
  Serial.println("Rapid-rise threshold: >= 5 cm/min (simulation only)");
  Serial.println("Rate calculation window: approximately 10 seconds");
  Serial.print("MQTT topic base: ");
  Serial.println(TOPIC_BASE);
  Serial.println();

  connectWiFi();

#if USE_HIVEMQ_CLOUD
  netClient.setInsecure();  // tanpa verifikasi sertifikat; cukup untuk simulasi
#endif
  mqtt.setServer(mqttHost, mqttPort);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);  // default 256 byte terlalu kecil untuk JSON telemetry
}

void loop() {
  maintainMqtt();

  if (millis() - lastSensorRead < SENSOR_INTERVAL_MS) {
    return;
  }
  lastSensorRead = millis();

  float distance = readFilteredDistance();

  if (distance < 0.0) {
    showStatus(SENSOR_ERROR);
    Serial.println("Sensor: ERROR - no valid echo received");
    publishTelemetry(-1.0, -1.0, SENSOR_ERROR, 0.0, false, SENSOR_ERROR);
    return;
  }

  float waterLevel = REFERENCE_HEIGHT_CM - distance;

  if (waterLevel < 0.0) waterLevel = 0.0;
  if (waterLevel > REFERENCE_HEIGHT_CM) {
    waterLevel = REFERENCE_HEIGHT_CM;
  }

  unsigned long now = millis();

  // Calculate trend against previous history before adding current sample.
  float riseRate = 0.0;
  bool rateIsValid = calculateRiseRate(waterLevel, now, riseRate);

  WaterStatus baseStatus = classifyWaterLevel(waterLevel);
  WaterStatus finalStatus =
    applyAdaptiveWarning(baseStatus, riseRate, rateIsValid);

  showStatus(finalStatus);

  // Add this reading after calculating the rate so it is not compared to itself.
  addLevelHistory(waterLevel, now);

  Serial.print("Distance: ");
  Serial.print(distance, 1);
  Serial.print(" cm | Water level: ");
  Serial.print(waterLevel, 1);
  Serial.print(" cm | Base status: ");
  Serial.print(statusToText(baseStatus));
  Serial.print(" | Rise rate: ");

  if (rateIsValid) {
    Serial.print(riseRate, 2);
    Serial.print(" cm/min");
  } else {
    Serial.print("WARMING UP");
  }

  Serial.print(" | Final status: ");
  Serial.println(statusToText(finalStatus));

  if (rateIsValid && riseRate >= RAPID_RISE_THRESHOLD_CM_MIN) {
    Serial.println("WARNING: Rapid water-level rise detected!");
  }

  publishTelemetry(distance, waterLevel, baseStatus,
                   riseRate, rateIsValid, finalStatus);
}
