import csv, io, os, secrets, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Africa/Addis_Ababa"))
LATE_AFTER = os.environ.get("LATE_AFTER", "09:15")
DB = os.environ.get("DB_PATH", "attendance.db")

app = Flask(__name__)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    con = g.pop("db", None)
    if con:
        con.close()


def init():
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS students(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,
          created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS attendance(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
          day TEXT NOT NULL,
          time TEXT NOT NULL,
          status TEXT NOT NULL,
          UNIQUE(student_id, day));
        """
    )
    con.commit()
    con.close()


init()


@app.get("/api/health")
def health():
    return jsonify(ok=True)


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
    cur = db().execute("INSERT INTO students(code, name) VALUES (?, ?)", (code, name))
    db().commit()
    return jsonify(id=cur.lastrowid, code=code, name=name), 201


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
    now = datetime.now(TZ)
    day, t = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    status = "late" if t > LATE_AFTER else "present"
    cur = db().execute(
        "INSERT OR IGNORE INTO attendance(student_id, day, time, status) VALUES (?, ?, ?, ?)",
        (s["id"], day, t, status),
    )
    db().commit()
    if cur.rowcount == 0:
        old = db().execute(
            "SELECT time, status FROM attendance WHERE student_id = ? AND day = ?", (s["id"], day)
        ).fetchone()
        return jsonify(ok=True, already=True, name=s["name"], time=old["time"], status=old["status"])
    return jsonify(ok=True, already=False, name=s["name"], time=t, status=status)


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
    w.writerows([tuple(r) for r in rows])
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)

