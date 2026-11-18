import base64, os, sqlite3
from datetime import datetime

import cv2
import numpy as np
from flask import jsonify, request

THRESHOLD = float(os.environ.get("FACE_THRESHOLD", "70"))
MAX_SAMPLES = 40
cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_model = {"sig": None, "rec": None}
if cascade.empty():
    raise RuntimeError("Face detector file failed to load. Install opencv-contrib-python-headless<5")


def decode(data_url):
    if not isinstance(data_url, str) or len(data_url) > 3_000_000:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[-1])
    except Exception:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)


def find_face(gray):
    if gray is None:
        return None
    h, w = gray.shape
    if w > 640:
        gray = cv2.resize(gray, (640, int(h * 640 / w)))
    faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
    if len(faces) == 0:
        return None
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    face = cv2.resize(gray[y:y + fh, x:x + fw], (200, 200))
    return cv2.equalizeHist(face)


def recognizer(con):
    count, last = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM face_samples").fetchone()
    if count == 0:
        return None
    if _model["sig"] != (count, last):
        imgs, labels = [], []
        for sid, blob in con.execute("SELECT student_id, img FROM face_samples"):
            imgs.append(cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_GRAYSCALE))
            labels.append(sid)
        rec = cv2.face.LBPHFaceRecognizer_create()
        rec.train(imgs, np.array(labels))
        _model.update(sig=(count, last), rec=rec)
    return _model["rec"]


def register(app, db, db_path, tz, late_after):
    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS face_samples(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
             img BLOB NOT NULL)"""
    )
    con.commit()
    con.close()

    def mark(sid):
        now = datetime.now(tz)
        day, t = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        status = "late" if t > late_after else "present"
        cur = db().execute(
            "INSERT OR IGNORE INTO attendance(student_id, day, time, status) VALUES (?, ?, ?, ?)",
            (sid, day, t, status),
        )
        db().commit()
        if cur.rowcount == 0:
            old = db().execute(
                "SELECT time, status FROM attendance WHERE student_id = ? AND day = ?", (sid, day)
            ).fetchone()
            return True, old["time"], old["status"]
        return False, t, status

    @app.post("/api/students/<int:sid>/face")
    def add_face(sid):
        if not db().execute("SELECT 1 FROM students WHERE id = ?", (sid,)).fetchone():
            return jsonify(error="Student not found"), 404
        n = db().execute("SELECT COUNT(*) FROM face_samples WHERE student_id = ?", (sid,)).fetchone()[0]
        if n >= MAX_SAMPLES:
            return jsonify(error="Enough samples already"), 400
        face = find_face(decode((request.json or {}).get("image")))
        if face is None:
            return jsonify(error="No face found"), 422
        ok, png = cv2.imencode(".png", face)
        db().execute("INSERT INTO face_samples(student_id, img) VALUES (?, ?)", (sid, png.tobytes()))
        db().commit()
        return jsonify(ok=True, count=n + 1), 201

    @app.delete("/api/students/<int:sid>/face")
    def clear_face(sid):
        db().execute("DELETE FROM face_samples WHERE student_id = ?", (sid,))
        db().commit()
        return jsonify(ok=True)

    @app.get("/api/faces")
    def face_counts():
        rows = db().execute("SELECT student_id, COUNT(*) c FROM face_samples GROUP BY student_id").fetchall()
        return jsonify({str(r["student_id"]): r["c"] for r in rows})

    @app.post("/api/face-scan")
    def face_scan():
        rec = recognizer(db())
        if rec is None:
            return jsonify(match=False, reason="notrained")
        face = find_face(decode((request.json or {}).get("image")))
        if face is None:
            return jsonify(match=False, reason="noface")
        sid, conf = rec.predict(face)
        if conf > THRESHOLD:
            return jsonify(match=False, reason="unknown", confidence=round(conf))
        s = db().execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
        if not s:
            return jsonify(match=False, reason="unknown")
        already, t, status = mark(sid)
        return jsonify(match=True, name=s["name"], time=t, status=status, already=already, confidence=round(conf))

