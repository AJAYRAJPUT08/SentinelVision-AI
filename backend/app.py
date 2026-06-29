import os
import cv2
import time
import numpy as np
from flask import Flask, Response, jsonify, send_file, request
from state import global_state
from recognition import get_recognizer

# Initialize Flask App
app = Flask(__name__, static_folder="../frontend", template_folder="../frontend")

# Ensure required directory structure exists
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWN_DIR = os.path.abspath(os.path.join(BASE_DIR, "../known_faces"))
UNKNOWN_DIR = os.path.abspath(os.path.join(BASE_DIR, "../unknown_faces"))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "../frontend"))

os.makedirs(KNOWN_DIR, exist_ok=True)
os.makedirs(UNKNOWN_DIR, exist_ok=True)

class CameraStream:
    """
    Continuous real video streaming generator.
    Strictly uses physical hardware webcam to ensure REAL LIVE RECOGNITION.
    """
    def __init__(self, camera_src=0):
        self.camera_src = camera_src
        self.cap = None
        self.init_camera()

    def init_camera(self):
        try:
            # Try to connect to real hardware camera
            self.cap = cv2.VideoCapture(self.camera_src)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                self.cap.set(cv2.CAP_PROP_FPS, 30)
                print(f"[SentinelVision] Real hardware webcam connected successfully on source {self.camera_src}")
            else:
                # Fallback to source 1 if 0 is occupied/unavailable
                self.cap = cv2.VideoCapture(1)
                if self.cap.isOpened():
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    self.cap.set(cv2.CAP_PROP_FPS, 30)
                    print("[SentinelVision] Real hardware webcam connected successfully on source 1")
                else:
                    print("[SentinelVision] CRITICAL ERROR: Could not find any real hardware webcam!")
        except Exception as e:
            print(f"[SentinelVision] Camera initialization alert: {e}")

    def generate_frames(self):
        recognizer = get_recognizer()
        while True:
            frame = None
            if self.cap and self.cap.isOpened():
                ret, img = self.cap.read()
                if ret and img is not None:
                    # Flip frame horizontally to act like a mirror/webcam
                    frame = cv2.flip(img, 1)
            
            if frame is None:
                # If camera totally disconnected, display a real error frame
                # Do NOT generate fake simulated security loop.
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                cv2.putText(frame, "NO HARDWARE WEBCAM DETECTED.", (350, 360), cv2.FONT_HERSHEY_SIMPLEX, 1, (40, 40, 255), 2)
                cv2.putText(frame, "PLEASE CONNECT CAMERA AND RESTART BACKEND.", (300, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 255), 2)
                time.sleep(1)

            # Process frame through the real Recognition Engine
            processed_frame = recognizer.process_frame(frame)
            
            # Encode frame to JPEG
            ret, buffer = cv2.imencode('.jpg', processed_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if ret:
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

camera_stream = CameraStream()

# Initialize the recognition engine (and load/train known_faces) immediately
# at startup - NOT lazily on first video frame. generate_frames() is a
# generator, so get_recognizer() inside it doesn't actually run until a
# client starts consuming /video. Until then, global_state.known_faces
# stayed at 0 even though known_faces/ was loaded correctly, causing the
# dashboard counter and the database page to disagree.
get_recognizer()

# ==========================================
# REQUIRED FLASK ROUTES
# ==========================================

@app.route("/")
def index():
    return send_file(os.path.join(FRONTEND_DIR, "index.html"))

@app.route("/video")
def video():
    return Response(camera_stream.generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/status")
def status():
    return jsonify(global_state.get_status_json())

@app.route("/script.js")
def serve_js():
    return send_file(os.path.join(FRONTEND_DIR, "script.js"), mimetype="application/javascript")

@app.route("/style.css")
def serve_css():
    return send_file(os.path.join(FRONTEND_DIR, "style.css"), mimetype="text/css")

@app.route("/photo/<path:filename>")
def serve_known_photo(filename):
    file_path = os.path.join(KNOWN_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    return "", 404

@app.route("/unknown_photo/<path:filename>")
def serve_unknown_photo(filename):
    file_path = os.path.join(UNKNOWN_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    return "", 404

@app.route("/api/known_faces")
def list_known_faces():
    """Returns one entry per registered person (grouping multiple enrollment
    photos of the same person, e.g. 'Ajay.jpg' + 'Ajay 2.jpg', under a single
    card) for frontend display."""
    files = [f for f in os.listdir(KNOWN_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    recognizer = get_recognizer()

    seen_names = {}
    for f in files:
        name = recognizer.person_key_for_file(f)
        if name not in seen_names:
            seen_names[name] = f

    result = []
    for name, f in seen_names.items():
        try:
            mtime = os.path.getmtime(os.path.join(KNOWN_DIR, f))
            enrolled = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))
        except:
            enrolled = "UNKNOWN"

        result.append({
            "id": f,
            "name": name,
            "filename": f,
            "photoUrl": f"/photo/{f}",
            "status": "AUTHORIZED",
            "role": recognizer.role_for(name),
            "enrolledAt": enrolled
        })
    return jsonify(result)

@app.route("/api/unknown_faces")
def list_unknown_faces():
    """Returns actual list of saved unknown face captures."""
    files = sorted([f for f in os.listdir(UNKNOWN_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=True)
    result = []
    for f in files:
        # Example format: unknown_20260101_120000.jpg
        parts = f.replace('.jpg', '').replace('.jpeg', '').replace('.png', '').split('_')
        timestamp = f
        if len(parts) >= 3:
            time_str = parts[2]
            # Format to HH:MM:SS
            if len(time_str) == 6:
                timestamp = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]} UTC"
                
        result.append({
            "id": f,
            "timestamp": timestamp,
            "photoUrl": f"/unknown_photo/{f}"
        })
    return jsonify(result)

@app.route("/api/add_face", methods=["POST"])
def add_face():
    """Enroll a new face dynamically into known_faces directory."""
    if 'image' not in request.files or 'name' not in request.form:
        return jsonify({"error": "Missing image or name parameter"}), 400
    
    img_file = request.files['image']
    name = request.form['name'].strip().replace(" ", "_")
    if not name or not img_file:
        return jsonify({"error": "Invalid input"}), 400
        
    filename = f"{name}.jpg"
    save_path = os.path.join(KNOWN_DIR, filename)
    img_file.save(save_path)
    
    # Trigger reload in recognizer
    get_recognizer().load_known_faces()
    return jsonify({"success": True, "name": name.replace("_", " ").upper(), "url": f"/photo/{filename}"})

if __name__ == "__main__":
    print("=========================================================")
    print("      SentinelVision AI REAL LIVE Surveillance Active")
    print("=========================================================")
    # Disable reloader if threading is enabled to prevent OpenCV capturing bugs on Windows
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False, use_reloader=False)
