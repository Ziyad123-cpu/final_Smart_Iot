import os
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import threading
import sqlite3
from datetime import datetime
import time

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
    conn.commit()
    conn.close()

init_db()

def insert_data_to_db(data):
    try:
        now = datetime.now()
        tanggal = now.strftime("%d-%m-%Y")
        hari = now.strftime("%A")
        waktu = now.strftime("%H:%M:%S")

        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("""
            INSERT INTO sensor_log 
            (tanggal, hari, waktu, moisture, soil_temp, air_temp, air_hum, pump_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tanggal,
            hari,
            waktu,
            data.get("moisturePercent", 0),
            data.get("soilTemperature", 0.0),
            data.get("suhuUdara", 0.0),
            data.get("kelembapanUdara", 0.0),
            data.get("pumpState", "MATI")
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
TOPIC_JADWAL = "irigasi/jadwal"

sensor_data = {
    "moisturePercent": 0,
    "soilTemperature": 0.0,
    "suhuUdara": 0.0,
    "kelembapanUdara": 0.0,
    "pumpState": "MATI",
    "tanggal": "-",
    "hari": "-",
    "waktu": "-"
}

jadwal_siram = {
    "jam": "--:--",
    "durasi": 60  # detik
}

def on_connect(client, userdata, flags, rc):
    print("MQTT CONNECTED:", rc)
    client.subscribe(TOPIC_SENSOR)

def on_message(client, userdata, msg):
    global sensor_data
    try:
        if msg.topic == TOPIC_SENSOR:
            data = json.loads(msg.payload.decode())
            sensor_data.update(data)
            insert_data_to_db(sensor_data)
    except Exception as e:
        print("MQTT ERROR:", e)

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

threading.Thread(
    target=mqtt_client.loop_forever,
    daemon=True
).start()

# ============================================
#           SCHEDULER JADWAL SIRAM
# ============================================

def scheduler_siram():
    last_trigger = None

    while True:
        now = datetime.now().strftime("%H:%M")
        jadwal = jadwal_siram["jam"]

        if jadwal != "--:--" and now == jadwal and last_trigger != now:
            print("⏰ JADWAL SIRAM AKTIF")

            mqtt_client.publish(TOPIC_POMPA, "ON")
            sensor_data["pumpState"] = "MENYALA 💦"

            time.sleep(jadwal_siram["durasi"])

            mqtt_client.publish(TOPIC_POMPA, "OFF")
            sensor_data["pumpState"] = "MATI"

            last_trigger = now

        time.sleep(20)

threading.Thread(
    target=scheduler_siram,
    daemon=True
).start()

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

@app.route("/get_history")
def get_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM sensor_log ORDER BY id DESC LIMIT 300")
        rows = c.fetchall()
        return jsonify(rows)
    except:
        return jsonify([])
    finally:
        conn.close()

@app.route("/pump/<action>")
def pump(action):
    if action.lower() == "on":
        mqtt_client.publish(TOPIC_POMPA, "ON")
        sensor_data["pumpState"] = "MENYALA 💦"
    else:
        mqtt_client.publish(TOPIC_POMPA, "OFF")
        sensor_data["pumpState"] = "MATI"
    return jsonify(sensor_data)

@app.route("/set_jadwal", methods=["POST"])
def set_jadwal():
    data = request.get_json()
    jam = data.get("jam")

    if not jam:
        return jsonify({"status": "error"})

    jadwal_siram["jam"] = jam
    mqtt_client.publish(TOPIC_JADWAL, jam)

    return jsonify({
        "status": "success",
        "jadwal": jam
    })

@app.route("/get_jadwal")
def get_jadwal():
    return jsonify(jadwal_siram)

# ============================================
#               RUN SERVER
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"RUNNING on port {port}...")
    app.run(host="0.0.0.0", port=port)
