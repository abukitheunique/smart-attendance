import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import g, jsonify, send_from_directory

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Africa/Addis_Ababa"))


def register(app, db):
    def denied():
        return jsonify(error="Not allowed"), 403

    @app.get("/api/admin/people")
    def admin_people():
        if g.user["role"] != "admin":
            return denied()
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        users = db().execute(
            "SELECT id, name, email, role FROM users "
            "WHERE role IN ('teacher', 'subdirector') AND status = 'approved' ORDER BY name").fetchall()
        rows = db().execute(
            """SELECT s.id, s.name, s.owner_id, COALESCE(a.status, 'absent') AS status
               FROM students s LEFT JOIN attendance a ON a.student_id = s.id AND a.day = ?
               ORDER BY s.name""", (day,)).fetchall()
        by = {}
        for r in rows:
            by.setdefault(r["owner_id"], []).append({"id": r["id"], "name": r["name"], "status": r["status"]})
        out = []
        for u in users:
            people = by.get(u["id"], [])
            out.append({**dict(u), "people": people,
                        "present": sum(1 for p in people if p["status"] != "absent")})
        return jsonify(day=day, users=out)

    @app.get("/api/admin/stats")
    def admin_stats():
        if g.user["role"] != "admin":
            return denied()

        def n(sql):
            return db().execute(sql).fetchone()["n"]

        return jsonify(
            pending=n("SELECT COUNT(*) AS n FROM users WHERE status = 'pending'"),
            teachers=n("SELECT COUNT(*) AS n FROM users WHERE role = 'teacher' AND status = 'approved'"),
            subdirectors=n("SELECT COUNT(*) AS n FROM users WHERE role = 'subdirector' AND status = 'approved'"),
            people=n("SELECT COUNT(*) AS n FROM students"),
            reports=n("SELECT COUNT(*) AS n FROM reports"))

    @app.get("/admin/people")
    def people_page():
        if g.user["role"] != "admin":
            return denied()
        return send_from_directory("static", "people.html")

    @app.get("/shell.js")
    def shell_js():
        return send_from_directory("static", "shell.js")

    @app.get("/shell.css")
    def shell_css():
        return send_from_directory("static", "shell.css")
