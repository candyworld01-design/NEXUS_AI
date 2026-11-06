import threading
from flask import Flask, jsonify
from config import PORT

app = Flask(__name__)

health_status = {
    "bot_active": False,
    "assistant_active": False,
    "pytgcalls_active": False
}

@app.route("/health", methods=["GET"])
def health_check():
    is_healthy = all(health_status.values())
    status_code = 200 if is_healthy else 503
    return jsonify({
        "status": "healthy" if is_healthy else "degraded",
        "components": health_status
    }), status_code

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def start_health_server():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
