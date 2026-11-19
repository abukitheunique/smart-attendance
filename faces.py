import base64
import os
import threading

import cv2
import numpy as np
from flask import g, jsonify, request

THRESHOLD = float(os.environ.get("FACE_THRESHOLD", "70"))
MAX_SAMPLES = 40
cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
if cascade.empty():
    raise RuntimeError("Face detector file failed to load. Install opencv-contrib-python-headless<5")
_models = {}
_lock = threading.Lock()


def decode(data_url):
    if not isinstance(data_url, str):
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
    found = cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))
    if len(found) == 0:
        return None
    x, y, fw, fh = max(found, key=lambda f: f[2] * f[3])
    face = cv2.resize(gray[y:y + fh, x:x + fw], (200, 200))
    return cv2.equalizeHist(face)


def recognizer(con, owner):
    if owner is None:
        where, params = "", ()
    else:
        where, params = " WHERE student_id IN (SELECT id FROM students WHERE owner_id = ?)", (owner,)
    row = con.execute("SELECT COUNT(*) AS c, COALESCE(MAX(id), 0) AS m FROM face_samples" + where, params).fetchone()
    if row["c"] == 0:
        return None
    sig = (row["c"], row["m"])
    with _lock:
        cached = _models.get(owner)
        if not cached or cached[0] != sig:
            imgs, labels = [], []
            for r in con.execute("SELECT student_id, img FROM face_samples" + where, params).fetchall():
                imgs.append(cv2.imdecode(np.frombuffer(r["img"], np.uint8), cv2.IMREAD_GRAYSCALE))
                labels.append(r["student_id"])
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.train(imgs, np.array(labels))
            _models[owner] = (sig, rec)
        return _models[owner][1]


def register(app, db, mark, owns):
    @app.post("/api/students/<int:sid>/face")
    def add_face(sid):
        if not owns(sid):
            return jsonify(error="Student not found"), 404
        n = db().execute("SELECT COUNT(*) AS c FROM face_samples WHERE student_id = ?", (sid,)).fetchone()["c"]
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
        if not owns(sid):
            return jsonify(error="Student not found"), 404
        db().execute("DELETE FROM face_samples WHERE student_id = ?", (sid,))
        db().commit()
        return jsonify(ok=True)

    @app.post("/api/face-scan")
    def face_scan():
        owner = None if g.user["role"] == "admin" else g.user["id"]
        rec = recognizer(db(), owner)
        if rec is None:
            return jsonify(match=False, reason="notrained")
        face = find_face(decode((request.json or {}).get("image")))
        if face is None:
            return jsonify(match=False, reason="noface")
        sid, conf = rec.predict(face)
        if conf > THRESHOLD:
            return jsonify(match=False, reason="unknown", confidence=round(conf))
        s = db().execute("SELECT name FROM students WHERE id = ?", (int(sid),)).fetchone()
        if not s:
            return jsonify(match=False, reason="unknown")
        already, t, status = mark(int(sid))
        return jsonify(match=True, name=s["name"], time=t, status=status, already=already, confidence=round(conf))
