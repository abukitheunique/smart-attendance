import csv, io, os, secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
from flask import (Flask, Response, g, jsonify, request, send_file,
                   send_from_directory)

import auth
import database
import faces

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Africa/Addis_Ababa"))
LATE_AFTER = os.environ.get("LATE_AFTER", "09:15")
MAX_IMPORT = 500
HEADERS = {"name", "names", "full name", "fullname", "student", "students", "teacher", "teachers"}

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


def owner_filter(alias=""):
    """SQL to AND into a WHERE clause: admins see everyone, others only their own list."""
    if g.user["role"] == "admin":
        return "", ()
    col = f"{alias}.owner_id" if alias else "owner_id"
    return f" AND {col} = ?", (g.user["id"],)


def owns(sid):
    f, p = owner_filter()
    row = db().execute("SELECT 1 AS x FROM students WHERE id = ?" + f, (sid,) + p).fetchone()
    return row is not None


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


faces.register(app, db, mark, owns)
auth.register(app, db)


@app.get("/api/health")
def health():
    try:
        db().execute("SELECT 1 AS x").fetchone()
        return jsonify(ok=True, database="postgres" if database.PG else "sqlite")
    except Exception:
        return jsonify(ok=False), 503


@app.get("/api/students")
def list_students():
    f, p = owner_filter()
    rows = db().execute("SELECT id, code, name FROM students WHERE 1 = 1" + f + " ORDER BY name", p).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/students")
def add_student():
    name = str((request.json or {}).get("name", "")).strip()
    if not 2 <= len(name) <= 80:
        return jsonify(error="Name must be 2-80 characters"), 400
    code = secrets.token_hex(6)
    row = db().execute(
        "INSERT INTO students(code, name, owner_id) VALUES (?, ?, ?) RETURNING id",
        (code, name, g.user["id"]),
    ).fetchone()
    db().commit()
    return jsonify(id=row["id"], code=code, name=name), 201


@app.post("/api/students/import")
def import_students():
    text = str((request.json or {}).get("csv", "")).lstrip("\ufeff")
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].strip()]
    if rows and rows[0][0].strip().lower() in HEADERS:
        rows = rows[1:]
    if not rows:
        return jsonify(error="No names found in the file"), 400
    if len(rows) > MAX_IMPORT:
        return jsonify(error=f"Too many rows (max {MAX_IMPORT})"), 400
    uid = g.user["id"]
    have = {r["name"].lower() for r in db().execute(
        "SELECT name FROM students WHERE owner_id = ?", (uid,)).fetchall()}
    added = skipped = 0
    for r in rows:
        name = r[0].strip()
        if not 2 <= len(name) <= 80 or name.lower() in have:
            skipped += 1
            continue
        have.add(name.lower())
        db().execute("INSERT INTO students(code, name, owner_id) VALUES (?, ?, ?)",
                     (secrets.token_hex(6), name, uid))
        added += 1
    db().commit()
    return jsonify(ok=True, added=added, skipped=skipped)


@app.delete("/api/students/<int:sid>")
def delete_student(sid):
    f, p = owner_filter()
    db().execute("DELETE FROM students WHERE id = ?" + f, (sid,) + p)
    db().commit()
    return jsonify(ok=True)


@app.get("/qr/<code>.png")
def qr_image(code):
    f, p = owner_filter()
    if not db().execute("SELECT 1 AS x FROM students WHERE code = ?" + f, (code,) + p).fetchone():
        return jsonify(error="Not found"), 404
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
    f, p = owner_filter("s")
    rows = db().execute(
        """SELECT s.id, s.name, a.time, COALESCE(a.status, 'absent') AS status
           FROM students s LEFT JOIN attendance a ON a.student_id = s.id AND a.day = ?
           WHERE 1 = 1""" + f + " ORDER BY s.name",
        (day,) + p,
    ).fetchall()
    people = [dict(r) for r in rows]
    counts = {k: sum(1 for x in people if x["status"] == k) for k in ("present", "late", "absent")}
    return jsonify(day=day, counts=counts, people=people)


def safe(v):
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


@app.get("/api/report.csv")
def report():
    start = request.args.get("from", "0000-01-01")
    end = request.args.get("to", "9999-12-31")
    f, p = owner_filter("s")
    rows = db().execute(
        """SELECT s.name, a.day, a.time, a.status FROM attendance a
           JOIN students s ON s.id = a.student_id
           WHERE a.day BETWEEN ? AND ?""" + f + " ORDER BY a.day DESC, s.name",
        (start, end) + p,
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


def page(name):
    return send_from_directory("static", name)


@app.get("/")
def kiosk():
    return page("index.html")


@app.get("/admin")
def admin():
    return page("admin.html")


@app.get("/dashboard")
def dashboard():
    return page("dashboard.html")


@app.get("/face")
def face_kiosk():
    return page("face.html")


@app.get("/enroll")
def enroll_page():
    return page("enroll.html")


@app.get("/login")
def login_page():
    return page("login.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
