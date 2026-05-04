# Smart Store IoT & Inventory Management System 🛒🔌

**Course:** Internet of Things (420-521-VA)  
**Developer:** Abdulmajeed Kakar  

## Project Overview
The Smart Store Inventory System is a centralized, full-stack IoT web application designed to solve the bottlenecks of traditional retail checkouts and manual environmental monitoring. The system integrates automated bulk RFID basket scanning for instant checkouts with real-time wireless IoT telemetry to continuously monitor the temperature and humidity of perishable inventory units.

## Key Features
*   **Automated Bulk Checkout:** Simultaneously scans multiple items using physical EPC tags to populate a digital cart instantly.
*   **Dual-Scanner Logic:** Implements asynchronous Python streams for flexible bulk scanning and strict Regex parsing for clean Admin tag registration.
*   **Environmental Dashboard:** Live temperature and humidity gauges powered by ESP32 microcontrollers and Bluetooth sensors.
*   **Automated Receipts:** Generates and dispatches digital customer receipts via SMTP upon checkout completion.
*   **Hardware-Agnostic Override:** Built-in modular UI allowing manual injection of sensor data and tag scans when physical hardware is disconnected.

## Technology Stack
*   **Backend:** Python 3, Flask Web Framework
*   **Frontend:** HTML, CSS, Bootstrap, Jinja2 Templating
*   **Database:** SQLite3
*   **Protocols & APIs:** Mosquitto (MQTT Broker), Pareto Anywhere (reelyActive Bluetooth API)

## Hardware Requirements
*   Raspberry Pi (Central Server & Broker Host)
*   USB RFID Scanner (Vendor ID: 0x0483) & 24-character EPC Tags
*   Standard Barcode Scanner
*   ESP32 Microcontrollers
*   DHT11/DHT22 Temperature & Humidity Sensors
*   Minew MSP01 Bluetooth Sensor

## Installation & Setup

### 1. Clone the Repository
```bash
git clone [https://github.com/majeedcs/IoTFinalProject.git](https://github.com/majeedcs/IoTFinalProject.git)
cd IoTFinalProject
