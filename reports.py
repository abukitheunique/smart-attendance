import csv, io, re, time

from flask import Response, g, jsonify, request, send_from_directory

import database

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BYTES = 1_000_000


def safe(v):
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def init_tables():
    pk = "SERIAL PRIMARY KEY" if database.PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c = database.Conn()
    c.execute(f"""CREATE TABLE IF NOT EXISTS reports(
        id {pk}, user_id INTEGER NOT NULL, from_day TEXT NOT NULL, to_day TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '', rows_count INTEGER NOT NULL, csv TEXT NOT NULL,
        created_at BIGINT NOT NULL)""")
    c.commit()
    c.close()


def register(app, db, owner_filter):
    init_tables()

    @app.post("/api/reports")
    def submit_report():
        if g.user["role"] == "admin":
            return jsonify(error="The Admin does not send reports"), 400
        d = request.json or {}
        start, end = str(d.get("from", "")), str(d.get("to", ""))
        note = str(d.get("note", "")).strip()[:300]
        if not (DAY_RE.match(start) and DAY_RE.match(end)) or start > end:
            return jsonify(error="Choose a valid date range"), 400
        f, p = owner_filter("s")
        rows = db().execute(
            """SELECT s.name, a.day, a.time, a.status FROM attendance a
               JOIN students s ON s.id = a.student_id
               WHERE a.day BETWEEN ? AND ?""" + f + " ORDER BY a.day, s.name",
            (start, end) + p).fetchall()
        if not rows:
            return jsonify(error="No attendance records in that range"), 400
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["name", "day", "time", "status"])
        w.writerows([[safe(r["name"]), r["day"], r["time"], r["status"]] for r in rows])
        text = out.getvalue()
        if len(text.encode()) > MAX_BYTES:
            return jsonify(error="Range too large. Choose a shorter range."), 400
        row = db().execute(
            "INSERT INTO reports(user_id, from_day, to_day, note, rows_count, csv, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (g.user["id"], start, end, note, len(rows), text, int(time.time()))).fetchone()
        db().commit()
        return jsonify(ok=True, id=row["id"], rows=len(rows)), 201

    @app.get("/api/my-reports")
    def my_reports():
        rows = db().execute(
            "SELECT id, from_day, to_day, note, rows_count, created_at FROM reports "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (g.user["id"],)).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/reports")
    def list_reports():
        if g.user["role"] != "admin":
            return jsonify(error="Not allowed"), 403
        rows = db().execute(
            """SELECT r.id, r.from_day, r.to_day, r.note, r.rows_count, r.created_at,
                      u.name AS sender, u.email, u.role
               FROM reports r JOIN users u ON u.id = r.user_id
               ORDER BY r.created_at DESC LIMIT 200""").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/reports/<int:rid>.csv")
    def download_report(rid):
        if g.user["role"] != "admin":
            return jsonify(error="Not allowed"), 403
        row = db().execute("SELECT csv, from_day, to_day FROM reports WHERE id = ?", (rid,)).fetchone()
        if not row:
            return jsonify(error="Not found"), 404
        name = f"report-{rid}-{row['from_day']}-to-{row['to_day']}.csv"
        return Response(row["csv"], mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/admin/reports")
    def reports_page():
        if g.user["role"] != "admin":
            return jsonify(error="Not allowed"), 403
        return send_from_directory("static", "reports.html")
