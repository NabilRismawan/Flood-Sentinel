# Flood-Sentinel
An IoT-based drainage water level monitoring and early warning prototype using ESP32, HC-SR04, MQTT (HiveMQ), and a real-time Python dashboard. Developed as part of Samsung Innovation Campus Batch 8.

# FloodSentinel 🌊

**IoT-Based Drainage Water Level Monitoring and Early Warning System**

FloodSentinel is an Internet of Things (IoT)-based prototype designed to monitor water levels in drainage channels and provide early warnings when water levels reach predefined thresholds.

The system uses an ESP32 and an HC-SR04 ultrasonic sensor to measure water levels, process monitoring data, and activate local warning indicators. Through MQTT communication using the HiveMQ Public Broker, monitoring data can be transmitted to a Python-based dashboard for real-time visualization.

This project is developed as part of **Samsung Innovation Campus Batch 8**.

---

## Project Overview

Flooding in residential areas can be associated with inadequate drainage monitoring and delayed awareness of rising water levels. FloodSentinel explores an IoT-based approach to provide continuous water level monitoring and multi-level warnings.

The prototype combines sensor-based monitoring, local warning mechanisms, MQTT communication, and a graphical monitoring dashboard.

### Key Features

- **Water Level Monitoring:** Measures the distance between the ultrasonic sensor and the water surface.
- **Water Level Classification:** Categorizes water conditions into Normal, Siaga, and Bahaya based on configurable thresholds.
- **Adaptive Warning:** Escalates warning levels when a rapid increase in water level is detected.
- **Local Alert System:** Uses LEDs and a buzzer to provide immediate visual and audible warnings.
- **MQTT Communication:** Transmits monitoring data through the HiveMQ Public Broker.
- **Real-Time Dashboard:** Displays water level measurements, system status, and monitoring graphs using Python.
- **Sensor Error Handling:** Identifies invalid sensor readings and reports sensor errors.

---

## System Architecture

The system follows this communication architecture:

```text
+---------------------------+
|       Wokwi Simulator     |
|                           |
|  ESP32 + HC-SR04          |
|  Green / Yellow / Red LED |
|  Buzzer                   |
+-------------+-------------+
              |
              | Wi-Fi
              |
              v
+---------------------------+
|    HiveMQ Public Broker   |
|                           |
|       MQTT Protocol       |
+-------------+-------------+
              |
              | MQTT Subscribe
              |
              v
+---------------------------+
|    Python Monitoring      |
|        Dashboard          |
|                           |
|  Tkinter + Matplotlib     |
|  Real-Time Monitoring     |
|  Water Level Graph        |
|  Status & Event Log       |
+---------------------------+
```

### Data Flow

1. The HC-SR04 sensor measures the distance to the water surface.
2. The ESP32 calculates the estimated water level using a predefined reference height.
3. The monitoring algorithm classifies the water condition.
4. LEDs and the buzzer respond according to the detected condition.
5. The ESP32 publishes telemetry and status information through MQTT.
6. The HiveMQ Public Broker forwards the messages to subscribed clients.
7. The Python dashboard receives and visualizes the monitoring data.

---

## Hardware Components

| Component | Function |
|---|---|
| ESP32 DevKit V1 | Main microcontroller |
| HC-SR04 | Ultrasonic distance measurement |
| Green LED | Normal condition indicator |
| Yellow LED | Warning condition indicator |
| Red LED | Danger condition indicator |
| Buzzer | Audible warning |
| 220 Ω Resistors | LED current limiting |
| 1 kΩ and 2 kΩ Resistors | HC-SR04 ECHO voltage divider |
| Breadboard and Jumper Wires | Circuit connections |

## Pin Configuration

| Component | ESP32 GPIO |
|---|---|
| HC-SR04 TRIG | GPIO 23 |
| HC-SR04 ECHO | GPIO 18 |
| Green LED | GPIO 25 |
| Yellow LED | GPIO 26 |
| Red LED | GPIO 27 |
| Buzzer | GPIO 14 |

**Note:** The HC-SR04 ECHO output uses a voltage divider to reduce its 5V signal to a level suitable for the ESP32 input.

---

## Monitoring Logic

FloodSentinel uses a reference height and configurable water level thresholds.

### Water Level Calculation

The estimated water level is calculated using:

```text
Water Level = Reference Height - Sensor Distance
```

The current simulation uses a reference height of 100 cm.

### Warning Classification

| Water Level | Status | Indicator |
|---|---|---|
| Below 30 cm | NORMAL | Green LED |
| 30–60 cm | SIAGA | Yellow LED |
| Above 60 cm | BAHAYA | Red LED + Buzzer |

### Adaptive Warning

The system also monitors the rate of water level increase.

When the detected rising rate reaches or exceeds 5 cm/min, the warning level can escalate by one level according to the implemented adaptive warning logic.

This feature is intended to demonstrate an additional warning mechanism based on water level trends.

**Important:** All thresholds and reference heights are simulation parameters. They have not been validated as real-world flood warning standards.

---

## Software and Technologies

| Technology | Purpose |
|---|---|
| ESP32 | Embedded processing |
| Arduino Framework | ESP32 firmware development |
| Wokwi | Hardware simulation |
| C++ | Embedded programming |
| MQTT | IoT messaging protocol |
| HiveMQ Public Broker | MQTT message broker |
| PubSubClient | MQTT client for ESP32 |
| Python | Dashboard development |
| Paho MQTT | MQTT client for Python |
| Tkinter | Graphical user interface |
| Matplotlib | Data visualization |

---

## MQTT Configuration

FloodSentinel uses the HiveMQ Public MQTT Broker for communication.

| Configuration | Value |
|---|---|
| Broker | `broker.hivemq.com` |
| Port | `1883` |
| Protocol | MQTT |
| Authentication | Not required |
| QoS | 0 |

### MQTT Topics

**Telemetry Topic**

```text
floodsentinel/sic-b8/demo01/device01/telemetry
```

**Status Topic**

```text
floodsentinel/sic-b8/demo01/device01/status
```

### Example Telemetry Payload

```json
{
  "device_id": "floodsentinel_01",
  "distance_cm": 54.5,
  "water_level_cm": 45.5,
  "rise_rate_cm_min": 2.3,
  "base_status": "SIAGA",
  "status": "SIAGA",
  "sensor_status": "NORMAL",
  "uptime_ms": 123456
}
```

The payload represents an example telemetry message and may differ depending on the firmware implementation.

---

## Getting Started

### 1. ESP32 Simulation

1. Open the FloodSentinel Wokwi project.
2. Verify the ESP32 circuit connections.
3. Ensure the firmware contains the required Wi-Fi and MQTT configuration.
4. Start the simulation.
5. Open the Serial Monitor to observe sensor readings and connection status.

The Wokwi simulation uses the virtual Wi-Fi network:

```cpp
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
```

### 2. MQTT Monitoring

To inspect published MQTT messages:

1. Open the [HiveMQ WebSocket Client](https://www.hivemq.com/demos/websocket-client/).
2. Connect to the public broker using its WebSocket endpoint.
3. Subscribe to the FloodSentinel telemetry topic.
4. Start the Wokwi simulation.
5. Observe incoming JSON messages.

The ESP32 uses MQTT over TCP, while the browser-based WebSocket client uses the broker's WebSocket endpoint.

### 3. Python Dashboard

Install Python and the required dependencies.

```bash
pip install -r requirements.txt
```

Run the dashboard using:

```bash
python main.py
```

The exact execution command and dependency list should match the final repository structure.

---

## Testing Scenarios

The following scenarios are intended for functional and integration testing:

| Test Scenario | Expected Behavior |
|---|---|
| Normal Water Level | Green LED and NORMAL status |
| Warning Water Level | Yellow LED and SIAGA status |
| High Water Level | Red LED, buzzer, and BAHAYA status |
| Rapid Water Level Rise | Adaptive warning escalation |
| Invalid Sensor Reading | SENSOR_ERROR handling |
| MQTT Disconnection | Connection loss is handled |
| MQTT Reconnection | Data transmission resumes |
| Dashboard Monitoring | Incoming telemetry is visualized |

Testing results should be documented based on actual simulation evidence and execution logs.

---

## Project Limitations

FloodSentinel is currently a simulation-based prototype. Several limitations must be considered:

- The system has not yet been tested using physical hardware in an actual drainage environment.
- HC-SR04 sensor accuracy under real water surface conditions has not been validated.
- Water level thresholds are demonstration parameters and require location-specific calibration.
- MQTT communication depends on internet connectivity.
- HiveMQ Public Broker is a shared testing service and is not intended for production deployment.
- The current prototype does not establish validated flood prediction capability.

---

## Future Development

Potential improvements include:

- Implementing the system using physical ESP32 hardware.
- Using waterproof and more suitable water level sensors.
- Developing weather-resistant hardware enclosures.
- Integrating rainfall sensors.
- Adding historical data storage and analysis.
- Implementing secure MQTT communication and device authentication.
- Developing mobile notifications.
- Conducting field calibration and testing in actual drainage channels.
- Exploring data-driven flood risk analysis using sufficient historical measurements.

---

## Project Team

**Samsung Innovation Campus Batch 8**

| Role | Responsibility |
|---|---|
| Project Manager & Research Analyst | Project planning, research, coordination, and documentation |
| Hardware & Embedded Systems Engineer | Hardware design, ESP32 firmware, and sensor monitoring |
| IoT Communication & Backend Engineer | MQTT communication, HiveMQ configuration, and data transmission |
| Dashboard Developer & Testing Engineer | Python dashboard, data visualization, and system testing |

---

## License

This project is developed for educational and prototyping purposes as part of Samsung Innovation Campus Batch 8.

---

**FloodSentinel — Smarter Drainage Monitoring Through IoT.**
