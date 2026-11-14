
import threading
from flask import Flask, jsonify
from config import PORT

app = Flask(__name__)
health_status = {
    "bot_active": False,
    "assistant_active": False,
    "pytgcalls_active": False,
}


def set_component(name, value):
    if name in health_status:
        health_status[name] = bool(value)


@app.route("/", methods=["GET"])
def home():
    return "NEXUS Music 2.0 is online", 200


@app.route("/health", methods=["GET"])
def health_check():
    healthy = all(health_status.values())
    return jsonify({
        "status": "healthy" if healthy else "starting_or_degraded",
        "components": dict(health_status),
    }), 200 if healthy else 503


def _run():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def start_health_server():
    thread = threading.Thread(target=_run, daemon=True, name="health-server")
    thread.start() 
