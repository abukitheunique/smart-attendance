import hashlib, hmac, os, re, secrets, time

from flask import g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import database

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
ROLES = {"teacher", "subdirector"}
OTP_TTL, OTP_TRIES, OTP_GAP = 600, 5, 60
HASH = "pbkdf2:sha256"

PUBLIC = {"/", "/login", "/signup", "/auth.css", "/shell.js", "/shell.css", "/api/login", "/api/health",
          "/api/signup/request", "/api/signup/verify", "/api/scan"}
AUTHED = {"/pending", "/api/me", "/api/logout"}
MANAGER_EXACT = {"/dashboard", "/enroll", "/face", "/api/students", "/api/today",
                 "/api/report.csv", "/api/face-scan", "/api/reports", "/api/my-reports"}
MANAGER_PREFIX = ("/api/students/", "/qr/")
ADMIN_BLOCKED = {"/dashboard", "/enroll", "/face"}


def norm(v):
    return str(v or "").strip().lower()


def send_mail(to, subject, text):
    print(f"\n[DEV MAIL] to={to} | {subject}\n{text}\n", flush=True)
    return True


def init_tables():
    pk = "SERIAL PRIMARY KEY" if database.PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c = database.Conn()
    c.execute(f"""CREATE TABLE IF NOT EXISTS users(
        id {pk}, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
        status TEXT NOT NULL, pw TEXT NOT NULL, created_at BIGINT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS otps(
        email TEXT PRIMARY KEY, code TEXT NOT NULL, expires BIGINT NOT NULL,
        tries INTEGER NOT NULL DEFAULT 0, last_sent BIGINT NOT NULL)""")
    if database.PG:
        c.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS owner_id INTEGER")
    else:
        try:
            c.execute("ALTER TABLE students ADD COLUMN owner_id INTEGER")
        except Exception:
            pass
    email = norm(os.environ.get("ADMIN_EMAIL"))
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if email and pw:
        c.execute(
            "INSERT INTO users(email, name, role, status, pw, created_at) VALUES (?, 'Director', 'admin', 'approved', ?, ?) "
            "ON CONFLICT (email) DO UPDATE SET pw = excluded.pw, role = 'admin', status = 'approved'",
            (email, generate_password_hash(pw, method=HASH), int(time.time())))
    c.commit()
    c.close()


def register(app, db):
    init_tables()

    def digest(email, code):
        return hmac.new(str(app.secret_key).encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        row = db().execute("SELECT id, email, name, role, status FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(row) if row else None

    @app.before_request
    def guard():
        p = request.path
        if p in PUBLIC:
            return None
        user = current_user()
        api = p.startswith(("/api/", "/qr/"))
        if not user:
            return (jsonify(error="Login required"), 401) if api else redirect("/login")
        g.user = user
        if p in AUTHED:
            return None
        if user["role"] == "admin" and user["status"] == "approved" and p in ADMIN_BLOCKED:
            return redirect("/admin")
        if user["status"] == "approved" and (user["role"] == "admin" or p in MANAGER_EXACT or p.startswith(MANAGER_PREFIX)):
            return None
        return (jsonify(error="Not allowed"), 403) if api else redirect("/pending")

    @app.post("/api/signup/request")
    def signup_request():
        email = norm((request.json or {}).get("email"))
        if not EMAIL_RE.match(email):
            return jsonify(error="Enter a valid email"), 400
        now = int(time.time())
        old = db().execute("SELECT last_sent FROM otps WHERE email = ?", (email,)).fetchone()
        if old and now - old["last_sent"] < OTP_GAP:
            return jsonify(error="Wait a minute before asking for another code"), 429
        exists = db().execute("SELECT 1 AS x FROM users WHERE email = ?", (email,)).fetchone()
        if not exists:
            code = f"{secrets.randbelow(10**6):06d}"
            db().execute(
                "INSERT INTO otps(email, code, expires, tries, last_sent) VALUES (?, ?, ?, 0, ?) "
                "ON CONFLICT (email) DO UPDATE SET code = excluded.code, expires = excluded.expires, "
                "tries = 0, last_sent = excluded.last_sent",
                (email, digest(email, code), now + OTP_TTL, now))
            db().commit()
            if not send_mail(email, "Your verification code",
                             f"Your code is {code}. It expires in 10 minutes."):
                return jsonify(error="Could not send the email. Try again later."), 502
        return jsonify(ok=True)

    @app.post("/api/signup/verify")
    def signup_verify():
        d = request.json or {}
        email, code = norm(d.get("email")), str(d.get("code", "")).strip()
        name, role, pw = str(d.get("name", "")).strip(), d.get("role"), str(d.get("password", ""))
        if not EMAIL_RE.match(email) or not 2 <= len(name) <= 80:
            return jsonify(error="Check your email and name"), 400
        if role not in ROLES:
            return jsonify(error="Choose Teacher or Sub-Director"), 400
        if not 8 <= len(pw) <= 128:
            return jsonify(error="Password must be 8-128 characters"), 400
        row = db().execute("SELECT code, expires, tries FROM otps WHERE email = ?", (email,)).fetchone()
        if not row or row["expires"] < time.time():
            return jsonify(error="Code expired. Request a new one."), 400
        if row["tries"] >= OTP_TRIES:
            return jsonify(error="Too many wrong tries. Request a new code."), 429
        if not hmac.compare_digest(row["code"], digest(email, code)):
            db().execute("UPDATE otps SET tries = tries + 1 WHERE email = ?", (email,))
            db().commit()
            return jsonify(error="Wrong code"), 400
        db().execute("DELETE FROM otps WHERE email = ?", (email,))
        user = db().execute(
            "INSERT INTO users(email, name, role, status, pw, created_at) VALUES (?, ?, ?, 'pending', ?, ?) "
            "ON CONFLICT (email) DO NOTHING RETURNING id",
            (email, name, role, generate_password_hash(pw, method=HASH), int(time.time()))).fetchone()
        db().commit()
        if not user:
            return jsonify(error="This email is already registered"), 409
        session.clear()
        session["uid"] = user["id"]
        return jsonify(ok=True)

    @app.post("/api/login")
    def login():
        d = request.json or {}
        email, pw = norm(d.get("email")), str(d.get("password", ""))
        row = db().execute("SELECT id, pw, role, status FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not check_password_hash(row["pw"], pw):
            time.sleep(1)
            return jsonify(error="Wrong email or password"), 401
        session.clear()
        session["uid"] = row["id"]
        return jsonify(ok=True, role=row["role"], status=row["status"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        return jsonify(g.user)

    @app.get("/api/applications")
    def applications():
        rows = db().execute(
            "SELECT id, email, name, role, status, created_at FROM users WHERE role <> 'admin' "
            "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/applications/<int:uid>")
    def decide(uid):
        status = {"approve": "approved", "reject": "rejected"}.get((request.json or {}).get("action"))
        if not status:
            return jsonify(error="Bad action"), 400
        row = db().execute(
            "UPDATE users SET status = ? WHERE id = ? AND role <> 'admin' RETURNING email, name",
            (status, uid)).fetchone()
        db().commit()
        if not row:
            return jsonify(error="Not found"), 404
        msg = ("Your application was approved. You can now log in and use your dashboard."
               if status == "approved" else "Your application was not approved.")
        send_mail(row["email"], "Your application", f"Hello {row['name']},\n\n{msg}")
        return jsonify(ok=True, status=status)

    @app.get("/signup")
    def signup_page():
        return send_from_directory("static", "signup.html")

    @app.get("/pending")
    def pending_page():
        return send_from_directory("static", "pending.html")

    @app.get("/admin/applications")
    def applications_page():
        return send_from_directory("static", "applications.html")

    @app.get("/auth.css")
    def auth_css():
        return send_from_directory("static", "auth.css")


