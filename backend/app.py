import os
from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import threading
import sqlite3
from datetime import datetime

# ============================================
#               FLASK SETUP
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")

app = Flask(
    __name__,
    static_folder=FRONTEND_DIR,
    static_url_path=""
)
CORS(app)

# ============================================
#               DATABASE SETUP
# ============================================

DB_PATH = os.path.join(BASE_DIR, "data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # TABEL UTAMA
    c.execute("""
        CREATE TABLE IF NOT EXISTS sensor_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT,
            hari TEXT,
            waktu TEXT,
            moisture REAL,
            soil_temp REAL,
            air_temp REAL,
            air_hum REAL,
            pump_state TEXT
        )
    """)

    # 🔧 MIGRASI KOLOM MODE (AMAN, TIDAK DUPLIKAT)
    try:
        c.execute("ALTER TABLE sensor_log ADD COLUMN mode TEXT DEFAULT 'MANUAL'")
    except sqlite3.OperationalError:
        pass  # kolom sudah ada

    conn.commit()
    conn.close()

init_db()

def insert_data_to_db(data):
    try:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()

        c.execute("""
            INSERT INTO sensor_log
            (tanggal, hari, waktu, moisture, soil_temp, air_temp, air_hum, pump_state, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now.strftime("%d-%m-%Y"),
            now.strftime("%A"),
            now.strftime("%H:%M:%S"),
            data.get("moisturePercent", 0),
            data.get("soilTemperature", 0.0),
            data.get("suhuUdara", 0.0),
            data.get("kelembapanUdara", 0.0),
            data.get("pumpState", "OFF"),
            data.get("mode", "MANUAL")
        ))

        conn.commit()
    except Exception as e:
        print("SQLite ERROR:", e)
    finally:
        conn.close()

# ============================================
#               MQTT SETUP
# ============================================

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883

TOPIC_SENSOR = "irigasi/sensor"
TOPIC_POMPA  = "irigasi/pompa"
TOPIC_MODE   = "irigasi/mode"

sensor_data = {
    "moisturePercent": 0,
    "soilTemperature": 0.0,
    "suhuUdara": 0.0,
    "kelembapanUdara": 0.0,
    "pumpState": "OFF",
    "mode": "MANUAL",
    "tanggal": "-",
    "hari": "-",
    "waktu": "-"
}

def on_connect(client, userdata, flags, rc):
    print("MQTT CONNECTED:", rc)
    client.subscribe(TOPIC_SENSOR)

def on_message(client, userdata, msg):
    global sensor_data
    try:
        payload = msg.payload.decode().strip()
        if not payload:
            return

        data = json.loads(payload)

        if msg.topic == TOPIC_SENSOR:
            sensor_data.update(data)
            print("DATA MQTT:", sensor_data)
            insert_data_to_db(sensor_data)

    except json.JSONDecodeError as e:
        print("MQTT JSON ERROR:", e)
    except Exception as e:
        print("MQTT ERROR:", e)

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

threading.Thread(target=mqtt_client.loop_forever, daemon=True).start()

# ============================================
#               FRONTEND ROUTES
# ============================================

@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/<path:path>")
def serve_static_files(path):
    return send_from_directory(FRONTEND_DIR, path)

# ============================================
#               API ROUTES
# ============================================

@app.route("/get_data")
def get_data():
    return jsonify(sensor_data)

@app.route("/pump/<action>")
def pump(action):
    if sensor_data["mode"] == "MANUAL":
        if action.lower() == "on":
            mqtt_client.publish(TOPIC_POMPA, "ON")
            sensor_data["pumpState"] = "ON"
        elif action.lower() == "off":
            mqtt_client.publish(TOPIC_POMPA, "OFF")
            sensor_data["pumpState"] = "OFF"
    return jsonify(sensor_data)

@app.route("/mode/<action>")
def mode(action):
    if action.lower() == "auto":
        sensor_data["mode"] = "AUTO"
        mqtt_client.publish(TOPIC_MODE, "AUTO")
    elif action.lower() == "manual":
        sensor_data["mode"] = "MANUAL"
        mqtt_client.publish(TOPIC_MODE, "MANUAL")
    return jsonify(sensor_data)

# ============================================
#               RUN SERVER
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"RUNNING on port {port}")
    app.run(host="0.0.0.0", port=port)
