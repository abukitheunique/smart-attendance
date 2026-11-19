import csv, io, os, secrets, time
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
from flask import (Flask, Response, g, jsonify, redirect, request, send_file,
                   send_from_directory, session)

import database
import faces

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Africa/Addis_Ababa"))
LATE_AFTER = os.environ.get("LATE_AFTER", "09:15")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)

database.init()


def db():
    if "db" not in g:
        g.db = database.Conn()
    return g.db


@app.teardown_appcontext
def close_db(_):
    con = g.pop("db", None)
    if con:
        con.close()


def mark(sid):
    now = datetime.now(TZ)
    day, t = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    status = "late" if t > LATE_AFTER else "present"
    cur = db().execute(
        "INSERT INTO attendance(student_id, day, time, status) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (student_id, day) DO NOTHING",
        (sid, day, t, status),
    )
    db().commit()
    if cur.rowcount == 0:
        old = db().execute(
            "SELECT time, status FROM attendance WHERE student_id = ? AND day = ?", (sid, day)
        ).fetchone()
        return True, old["time"], old["status"]
    return False, t, status


faces.register(app, db, mark)

PUBLIC = {"/", "/face", "/login", "/api/scan", "/api/face-scan", "/api/health", "/api/login"}


@app.before_request
def guard():
    if request.path in PUBLIC or session.get("admin"):
        return None
    if request.path.startswith(("/api/", "/qr/")):
        return jsonify(error="Login required"), 401
    return redirect("/login")


@app.post("/api/login")
def login():
    if not ADMIN_PASSWORD:
        return jsonify(error="ADMIN_PASSWORD is not set on the server"), 503
    pw = str((request.json or {}).get("password", ""))
    if not secrets.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
        time.sleep(1)
        return jsonify(error="Wrong password"), 401
    session["admin"] = True
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    try:
        db().execute("SELECT 1 AS x").fetchone()
        return jsonify(ok=True, database="postgres" if database.PG else "sqlite")
    except Exception:
        return jsonify(ok=False), 503


@app.get("/api/students")
def list_students():
    rows = db().execute("SELECT id, code, name FROM students ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/students")
def add_student():
    name = str((request.json or {}).get("name", "")).strip()
    if not 2 <= len(name) <= 80:
        return jsonify(error="Name must be 2-80 characters"), 400
    code = secrets.token_hex(6)
    row = db().execute("INSERT INTO students(code, name) VALUES (?, ?) RETURNING id", (code, name)).fetchone()
    db().commit()
    return jsonify(id=row["id"], code=code, name=name), 201


@app.delete("/api/students/<int:sid>")
def delete_student(sid):
    db().execute("DELETE FROM students WHERE id = ?", (sid,))
    db().commit()
    return jsonify(ok=True)


@app.get("/qr/<code>.png")
def qr_image(code):
    img = qrcode.make(code, box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.post("/api/scan")
def scan():
    code = str((request.json or {}).get("code", "")).strip()
    s = db().execute("SELECT id, name FROM students WHERE code = ?", (code,)).fetchone()
    if not s:
        return jsonify(error="Unknown code"), 404
    already, t, status = mark(s["id"])
    return jsonify(ok=True, already=already, name=s["name"], time=t, status=status)


@app.get("/api/today")
def today():
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    rows = db().execute(
        """SELECT s.id, s.name, a.time, COALESCE(a.status, 'absent') AS status
           FROM students s LEFT JOIN attendance a ON a.student_id = s.id AND a.day = ?
           ORDER BY s.name""",
        (day,),
    ).fetchall()
    people = [dict(r) for r in rows]
    counts = {k: sum(1 for p in people if p["status"] == k) for k in ("present", "late", "absent")}
    return jsonify(day=day, counts=counts, people=people)


def safe(v):
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


@app.get("/api/report.csv")
def report():
    start = request.args.get("from", "0000-01-01")
    end = request.args.get("to", "9999-12-31")
    rows = db().execute(
        """SELECT s.name, a.day, a.time, a.status FROM attendance a
           JOIN students s ON s.id = a.student_id
           WHERE a.day BETWEEN ? AND ? ORDER BY a.day DESC, s.name""",
        (start, end),
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name", "day", "time", "status"])
    w.writerows([[safe(r["name"]), r["day"], r["time"], r["status"]] for r in rows])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance.csv"},
    )


@app.get("/")
def kiosk():
    return send_from_directory("static", "index.html")


@app.get("/admin")
def admin():
    return send_from_directory("static", "admin.html")


@app.get("/face")
def face_kiosk():
    return send_from_directory("static", "face.html")


@app.get("/enroll")
def enroll_page():
    return send_from_directory("static", "enroll.html")


@app.get("/login")
def login_page():
    return send_from_directory("static", "login.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
