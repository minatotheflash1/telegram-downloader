"""Companion website for the telegram-downloader bot (Minato Downloader) — full version.

Features: download with dynamic quality/format (only options that actually
exist for that video are shown), video/audio converter via ffmpeg, thumbnail,
login/register with hashed passwords, profile, daily bonus, redeem codes,
download history, favourites, plans page, admin dashboard, dark/light theme,
and live Telegram bot status. Bot stats come from the shared data/bot.db.

Run: python webapp.py  (127.0.0.1:8000)
Public URL: cloudflared tunnel --url http://127.0.0.1:8000
"""
import glob
import hashlib
import os
import shutil
import sqlite3
import uuid
from datetime import date

import yt_dlp
from flask import Flask, after_this_request, jsonify, redirect, render_template, request, send_file, session, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "bot.db")
WEB_DB = os.path.join(BASE, "data", "web.db")
# Vercel's filesystem is read-only except /tmp, so downloads must go
# there — using BASE/web_downloads caused a 500 before any download
# code ran. /tmp works locally too; files are deleted after each request.
import tempfile
WEB_DL_DIR = os.path.join(tempfile.gettempdir(), "web_downloads")
MAX_WEB_MB = 49.5
TELEGRAM_BOT = "shortsgen_ai_bot"
SITE_NAME = "Minato Downloader"

app = Flask(__name__)
app.secret_key = os.environ.get("WEB_SECRET_KEY", "shortsgen-dev-secret-change-me")


def _is_pg():
    return (os.environ.get("DATABASE_URL") or "").startswith("postgres")


class _Row(dict):
    """Row supporting both row["col"] and row[0] access (sqlite Row compat)."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, cur, pg):
        self._cur, self._pg = cur, pg
    def fetchone(self):
        r = self._cur.fetchone()
        if r is None:
            return None
        if self._pg:
            return _Row(r)
        return _Row(zip([d[0] for d in self._cur.description], r)) if self._cur.description else _Row({})
    def fetchall(self):
        rows = self._cur.fetchall()
        out = []
        for r in rows:
            if self._pg:
                out.append(_Row(r))
            else:
                out.append(_Row(zip([d[0] for d in self._cur.description], r)) if self._cur.description else _Row({}))
        return out


class _Conn:
    def __init__(self, con, pg):
        self._con, self._pg = con, pg
    def execute(self, sql, params=()):
        if self._pg:
            sql = sql.replace("?", "%s")
        cur = self._con.cursor()
        cur.execute(sql, params)
        return _Cursor(cur, self._pg)
    def executescript(self, sql):
        cur = self._con.cursor()
        cur.executescript(sql)
    def commit(self):
        self._con.commit()
    def close(self):
        self._con.close()


def web_db():
    if _is_pg():
        import psycopg2
        from psycopg2.extras import RealDictCursor
        con = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)
        c = con.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS web_users(id SERIAL PRIMARY KEY, username TEXT UNIQUE, pw TEXT, telegram TEXT, plan TEXT DEFAULT 'Free', coins INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, created TEXT);
        CREATE TABLE IF NOT EXISTS web_history(id SERIAL PRIMARY KEY, user_id INTEGER, url TEXT, title TEXT, fmt TEXT, size_mb REAL, created TEXT);
        CREATE TABLE IF NOT EXISTS web_favs(id SERIAL PRIMARY KEY, user_id INTEGER, url TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS web_claims(id SERIAL PRIMARY KEY, user_id INTEGER, day TEXT, UNIQUE(user_id, day));
        CREATE TABLE IF NOT EXISTS web_redeems(id SERIAL PRIMARY KEY, user_id INTEGER, code TEXT, UNIQUE(user_id, code));
        CREATE TABLE IF NOT EXISTS web_cookies(id SERIAL PRIMARY KEY, content TEXT, updated TEXT);
        """)
        con.commit()
        return _Conn(con, True)
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    con = sqlite3.connect(WEB_DB, timeout=5)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS web_users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw TEXT, telegram TEXT, plan TEXT DEFAULT 'Free', coins INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS web_history(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, title TEXT, fmt TEXT, size_mb REAL, created TEXT);
    CREATE TABLE IF NOT EXISTS web_favs(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS web_claims(id INTEGER PRIMARY KEY, user_id INTEGER, day TEXT, UNIQUE(user_id, day));
    CREATE TABLE IF NOT EXISTS web_redeems(id INTEGER PRIMARY KEY, user_id INTEGER, code TEXT, UNIQUE(user_id, code));
    CREATE TABLE IF NOT EXISTS web_cookies(id INTEGER PRIMARY KEY, content TEXT, updated TEXT);
    """)
    con.commit()
    # Wrap sqlite in the same unified interface
    class _SqliteConn(_Conn):
        def execute(self, sql, params=()):
            cur = self._con.cursor()
            cur.execute(sql, params)
            return _Cursor(cur, False)
    return _SqliteConn(con, False)


def bot_stats():
    users = downloads = 0
    if _is_pg():
        try:
            import psycopg2
            con = psycopg2.connect(os.environ["DATABASE_URL"])
            cur = con.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(total_downloads),0) FROM users")
            row = cur.fetchone()
            users, downloads = row[0], row[1]
            con.close()
        except Exception:
            pass
    else:
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT COUNT(*), COALESCE(SUM(total_downloads),0) FROM users").fetchone()
            users, downloads = row[0], row[1]
            con.close()
        except Exception:
            pass
    bot_running = False
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "host-telegram-downloader" in cmd and "main.py" in cmd:
                bot_running = True
                break
    except Exception:
        pass
    return users, downloads, bot_running


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    con = web_db()
    row = con.execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()
    con.close()
    if not row:
        return None
    u = dict(row)
    con = web_db()
    today = str(date.today())
    from datetime import timedelta as _td
    _tomorrow = str(date.today() + _td(days=1))
    u["today"] = con.execute("SELECT COUNT(*) AS c FROM web_history WHERE user_id=? AND created >= ? AND created < ?", (uid, today, _tomorrow)).fetchone()["c"]
    con.close()
    u["daily_limit"] = 9999 if u["plan"] == "Premium" else (20 if u["plan"] == "Bronze" else 5)
    u["web_downloads"] = u["today"]  # today count shown on profile; full history on /history
    return u


def ctx(**kw):
    users, downloads, bot_running = bot_stats()
    d = {"bot": TELEGRAM_BOT, "site_name": SITE_NAME, "users": users, "downloads": downloads, "bot_running": bot_running,
         "max_mb": MAX_WEB_MB, "user": current_user()}
    d.update(kw)
    return d


def hash_pw(pw):
    return hashlib.sha256(("shortsgen:" + pw).encode()).hexdigest()


def ffmpeg_location():
    """Return a directory with an ffmpeg binary, or None.

    Local/PC installs usually have system ffmpeg. Vercel does not, so
    the imageio-ffmpeg pip package (installed from requirements.txt)
    provides a bundled binary there. Without this, video+audio merge
    and MP3 conversion are impossible on Vercel — YouTube videos like
    Rick Astley have no combined MP4 format at all."""
    import shutil
    if shutil.which("ffmpeg"):
        return None
    try:
        import imageio_ffmpeg
        # yt-dlp looks for an executable literally named "ffmpeg" in
        # ffmpeg_location, but imageio's binary is named
        # ffmpeg-linux-x86_64-... — so copy it to /tmp/ffmpeg first.
        src = imageio_ffmpeg.get_ffmpeg_exe()
        dst = os.path.join(tempfile.gettempdir(), "ffmpeg")
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o755)
        return tempfile.gettempdir()
    except Exception:
        return None


def yt_opts(extra, android_fallback=False):
    """yt-dlp options. By default the web client is used (full quality).

    If android_fallback is True, the android/ios player clients are
    used instead — YouTube's web client now often returns 'Sign in to
    confirm you're not a bot' from datacentre IPs (e.g. Vercel), and
    the android/ios clients bypass that check for public videos.
    If a video still needs login, a cookies.txt next to webapp.py
    (owner's own cookies) is used.
    """
    opts = dict(extra)
    if android_fallback:
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "ios", "web"]}}
    # Cookies, in priority order: cookies.txt file, then cookies saved
    # in the DB via /cookies (this is what makes Vercel work, where
    # the filesystem is ephemeral and a file upload would not persist).
    if os.path.exists(os.path.join(BASE, "cookies.txt")):
        opts["cookiefile"] = os.path.join(BASE, "cookies.txt")
    else:
        try:
            con = web_db()
            row = con.execute("SELECT content FROM web_cookies ORDER BY id DESC LIMIT 1").fetchone()
            con.close()
            if row and row["content"]:
                import tempfile
                path = os.path.join(tempfile.gettempdir(), "minato-cookies.txt")
                with open(path, "w") as f:
                    f.write(row["content"])
                opts["cookiefile"] = path
        except Exception:
            pass
    return opts


def _is_bot_error(exc):
    msg = str(exc).lower()
    return "not a bot" in msg or "sign in to confirm" in msg


def extract_with_fallback(opts, url, download):
    """Try web client first (full quality); on YouTube bot-check,
    retry once with android/ios clients."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)
    except Exception as exc:
        if not _is_bot_error(exc):
            raise
        fallback = yt_opts(opts, android_fallback=True)
        with yt_dlp.YoutubeDL(fallback) as ydl:
            return ydl.extract_info(url, download=download)


def extract_pin_image(url):
    """Fallback for Pinterest image pins: yt-dlp fails with 'No video formats
    found!' on image-only pins, so pull the largest pinimg image from the pin page."""
    if "pinterest." not in url and "pin.it" not in url:
        return None
    try:
        import re
        import requests
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}, timeout=30)
        if r.status_code != 200:
            return None
        imgs = set(re.findall(r"https://i\.pinimg\.com/[^\s\"'<>]+", r.text))
        # Prefer the main pin image (same hash, largest size bucket), not related pins.
        candidates = [u for u in imgs if "/1200x/" in u or "/originals/" in u]
        if not candidates:
            candidates = [u for u in imgs if "/736x/" in u]
        if not candidates:
            return None
        return sorted(candidates)[0]
    except Exception:
        return None


@app.route("/")
def home():
    return render_template("index.html", **ctx(error=None, ok=request.args.get("ok"), prefill_url=request.args.get("url", "")))


@app.route("/api/status")
def api_status():
    users, downloads, bot_running = bot_stats()
    return jsonify({"bot": TELEGRAM_BOT, "users": users, "bot_downloads": downloads, "bot_running": bot_running})


@app.route("/api/formats", methods=["POST"])
def api_formats():
    """Return ONLY the qualities/formats that actually exist for this URL."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "Please paste a full http(s) link."}), 400
    opts = yt_opts({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
                    "nocheckcertificate": True, "socket_timeout": 60})
    try:
        info = extract_with_fallback(opts, url, download=False)
    except Exception as exc:
        if _is_bot_error(exc):
            return jsonify({"error": "YouTube ei video-r jonno bot-check chaiche (login chara khola jacche na). Eta login/age-restricted ba YouTube-er IP block hote pare — nijer YouTube cookies (cookies.txt) dile tobei khulbe."}), 400
        pin_img = extract_pin_image(url)
        if pin_img:
            return jsonify({"title": "Pinterest image pin", "qualities": [],
                            "formats": [{"value": "thumb", "label": "Image only — JPG (this pin has no video)"}],
                            "pin_image": pin_img})
        return jsonify({"error": f"Could not read this link: {exc}"}), 400
    if not info:
        return jsonify({"error": "Could not read this link."}), 400
    heights = sorted({f.get("height") for f in (info.get("formats") or []) if f.get("vcodec") not in (None, "none") and f.get("height")}, reverse=True)
    has_audio = any(f.get("acodec") not in (None, "none") for f in (info.get("formats") or []))
    has_thumb = bool(info.get("thumbnail"))
    formats = []
    if heights:
        formats.append({"value": "vid", "label": "Video — MP4 (video + audio merged)"})
    if has_audio:
        formats.append({"value": "aud", "label": "Audio only — MP3 converter (ffmpeg)"})
    if has_thumb:
        formats.append({"value": "thumb", "label": "Thumbnail only — image"})
    if not formats:
        return jsonify({"error": "No downloadable video, audio or thumbnail was found for this link."}), 400
    return jsonify({"title": info.get("title") or "", "qualities": heights, "formats": formats})


@app.route("/download", methods=["POST"])
def download():
    url = (request.form.get("url") or "").strip()
    fmt = request.form.get("fmt") or "vid"
    quality = request.form.get("quality") or "720"
    if not url.startswith("http"):
        return render_template("index.html", **ctx(error="Please paste a full http(s) link.")), 400
    u = current_user()
    if u and u["today"] >= u["daily_limit"]:
        return render_template("index.html", **ctx(error=f"Daily web limit reached ({u['daily_limit']}/day for {u['plan']}). Try again tomorrow, or use the Telegram bot.")), 400
    dl_dir = os.path.join(WEB_DL_DIR, uuid.uuid4().hex[:12])

    @after_this_request
    def _cleanup(response):
        shutil.rmtree(dl_dir, ignore_errors=True)
        return response

    try:
        os.makedirs(dl_dir, exist_ok=True)
        # Converter logic: quality cap only if it is a number; 'best' = no cap.
        cap = f"[height<={quality}]" if quality.isdigit() else ""
        _ffloc = ffmpeg_location()
        has_ffmpeg = bool(shutil.which("ffmpeg")) or bool(_ffloc)
        if fmt == "vid":
            # Merge format is required: most YouTube videos have no
            # combined video+audio file. Without ffmpeg this fails
            # honestly instead of returning a silent video-only file.
            fmt_str = f"bestvideo{cap}[ext=mp4]+bestaudio/bestvideo{cap}+bestaudio/bestvideo+bestaudio/best"
            merge = "mp4"
        elif fmt == "aud":
            fmt_str = "bestaudio/best"
            merge = None
        else:
            fmt_str = "best"
            merge = None
        opts = yt_opts({"outtmpl": os.path.join(dl_dir, "%(id)s.%(ext)s"), "quiet": True, "no_warnings": True,
                        "noplaylist": True, "nocheckcertificate": True, "socket_timeout": 120,
                        "retries": 10, "fragment_retries": 10, "extractor_retries": 5,
                        "format": fmt_str, "merge_output_format": merge})
        if _ffloc:
            opts["ffmpeg_location"] = _ffloc
        if fmt == "aud" and has_ffmpeg:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if fmt == "thumb":
            # Thumbnail needs metadata only: setting a format makes yt-dlp fail
            # on videos whose format list is incomplete (YouTube 429 / no JS runtime).
            opts.pop("format", None)
            opts["skip_download"] = True
        info = extract_with_fallback(opts, url, download=(fmt != "thumb"))
        if not info:
            raise Exception("Could not read this link.")
        if fmt == "thumb":
            thumb = info.get("thumbnail")
            if not thumb:
                raise Exception("No thumbnail found for this link.")
            import requests
            resp = requests.get(thumb, timeout=30)
            resp.raise_for_status()
            data = resp.content
            if u:
                con = web_db()
                from datetime import datetime as _dt
                con.execute("INSERT INTO web_history(user_id,url,title,fmt,size_mb,created) VALUES(?,?,?,?,?,?)",
                            (u["id"], url, info.get("title") or "", "thumb", round(len(data) / (1024 * 1024), 2), _dt.now().strftime("%Y-%m-%d %H:%M:%S")))
                con.commit()
                con.close()
            from io import BytesIO
            return send_file(BytesIO(data), as_attachment=True, download_name="thumbnail.jpg")
        files = [f for f in glob.glob(os.path.join(dl_dir, "*")) if not f.endswith((".part", ".ytdl", ".tmp"))]
        files = [f for f in files if not f.lower().endswith((".jpg", ".png", ".webp"))]
        if not files:
            raise Exception("Download failed - no file was produced.")
        path = max(files, key=os.path.getsize)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > MAX_WEB_MB:
            raise Exception(f"File is {size_mb:.1f} MB, over the {MAX_WEB_MB} MB web limit. Use the Telegram bot for larger files if your plan allows it.")
        if u:
            con = web_db()
            from datetime import datetime as _dt
            con.execute("INSERT INTO web_history(user_id,url,title,fmt,size_mb,created) VALUES(?,?,?,?,?,?)",
                        (u["id"], url, info.get("title") or "", fmt, round(size_mb, 2), _dt.now().strftime("%Y-%m-%d %H:%M:%S")))
            con.commit()
            con.close()
        name = f"{info.get('title') or 'download'}"[:80].strip() + os.path.splitext(path)[1]
        # Read into memory first: the cleanup hook deletes dl_dir, and
        # on serverless the file may be gone before the body is sent.
        with open(path, "rb") as f:
            data = f.read()
        from io import BytesIO
        return send_file(BytesIO(data), as_attachment=True, download_name=name)
    except Exception as exc:
        if fmt == "thumb":
            pin_img = extract_pin_image(url)
            if pin_img:
                try:
                    import requests
                    resp = requests.get(pin_img, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                    resp.raise_for_status()
                    path = os.path.join(dl_dir, "pinterest-image.jpg")
                    with open(path, "wb") as f:
                        f.write(resp.content)
                    if u:
                        con = web_db()
                        from datetime import datetime as _dt2
                        con.execute("INSERT INTO web_history(user_id,url,title,fmt,size_mb,created) VALUES(?,?,?,?,?,?)",
                                    (u["id"], url, "Pinterest image pin", "thumb", round(len(resp.content) / (1024 * 1024), 2), _dt2.now().strftime("%Y-%m-%d %H:%M:%S")))
                        con.commit()
                        con.close()
                    return send_file(path, as_attachment=True, download_name="pinterest-image.jpg")
                except Exception:
                    pass
        msg = str(exc)
        if _is_bot_error(exc):
            msg = "YouTube ei video-r jonno bot-check chaiche (login chara khola jacche na). Admin /cookies page-e nijer YouTube cookies save korle khulbe, na hole local/PC version-e try koro."
        elif "Requested format is not available" in msg:
            msg = "That quality/format is not available for this video. Press 'Check available quality & format' first and pick only a shown option."
        elif "timed out" in msg.lower():
            msg = "The site took too long to respond. Try again in a bit."
        return render_template("index.html", **ctx(error=f"Download failed: {msg}")), 400


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        tg = (request.form.get("telegram") or "").strip()
        if not username or len(pw) < 4:
            return render_template("register.html", **ctx(error="Username and a password of at least 4 characters are required.")), 400
        con = web_db()
        is_admin = 1 if con.execute("SELECT COUNT(*) FROM web_users").fetchone()[0] == 0 else 0
        try:
            con.execute("INSERT INTO web_users(username,pw,telegram,is_admin,created) VALUES(?,?,?,?,?)",
                        (username, hash_pw(pw), tg, is_admin, str(date.today())))
            con.commit()
        except sqlite3.IntegrityError:
            con.close()
            return render_template("register.html", **ctx(error="That username is already taken.")), 400
        uid = con.execute("SELECT id FROM web_users WHERE username=?", (username,)).fetchone()[0]
        con.close()
        session["uid"] = uid
        return redirect(url_for("profile"))
    return render_template("register.html", **ctx(error=None))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        con = web_db()
        row = con.execute("SELECT * FROM web_users WHERE username=? AND pw=?",
                          ((request.form.get("username") or "").strip(), hash_pw(request.form.get("password") or ""))).fetchone()
        con.close()
        if not row:
            return render_template("login.html", **ctx(error="Wrong username or password.")), 400
        session["uid"] = row["id"]
        return redirect(url_for("profile"))
    return render_template("login.html", **ctx(error=None))


@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("home"))


@app.route("/profile")
def profile():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    con = web_db()
    favs = con.execute("SELECT * FROM web_favs WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall()
    con.close()
    return render_template("profile.html", **ctx(favs=favs, ok=request.args.get("ok")))


@app.route("/daily-bonus", methods=["POST"])
def daily_bonus():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    con = web_db()
    try:
        con.execute("INSERT INTO web_claims(user_id,day) VALUES(?,?)", (u["id"], str(date.today())))
        con.execute("UPDATE web_users SET coins=coins+1 WHERE id=?", (u["id"],))
        con.commit()
        msg = "Daily bonus claimed: +1 coin."
    except sqlite3.IntegrityError:
        msg = "Already claimed today. Come back tomorrow."
    con.close()
    return redirect(url_for("profile", ok=msg))


@app.route("/redeem", methods=["POST"])
def redeem():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    code = (request.form.get("code") or "").strip().upper()
    con = web_db()
    if code == "WELCOME10":
        try:
            con.execute("INSERT INTO web_redeems(user_id,code) VALUES(?,?)", (u["id"], code))
            con.execute("UPDATE web_users SET coins=coins+10 WHERE id=?", (u["id"],))
            con.commit()
            msg = "Redeemed WELCOME10: +10 coins."
        except sqlite3.IntegrityError:
            msg = "You already redeemed WELCOME10."
    else:
        msg = "Invalid or expired code."
    con.close()
    return redirect(url_for("profile", ok=msg))


@app.route("/favourite", methods=["POST"])
def favourite():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    url = (request.form.get("url") or "").strip()
    if url:
        con = web_db()
        from datetime import datetime as _dt3
        try:
            con.execute("INSERT INTO web_favs(user_id,url,created) VALUES(?,?,?)", (u["id"], url, _dt3.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
        con.commit()
        con.close()
    return redirect(url_for("profile", ok="Saved to favourites."))


@app.route("/history")
def history():
    u = current_user()
    rows = []
    if u:
        con = web_db()
        rows = con.execute("SELECT * FROM web_history WHERE user_id=? ORDER BY id DESC LIMIT 50", (u["id"],)).fetchall()
        con.close()
    return render_template("history.html", **ctx(rows=rows))


@app.route("/plans")
def plans():
    return render_template("plans.html", **ctx())


@app.route("/cookies", methods=["GET", "POST"])
def cookies_page():
    """Admin-only: save the owner's own YouTube cookies (Netscape
    cookies.txt format) into the DB so Vercel downloads can use them.

    Cookies are sensitive — never share someone else's, and if they
    leak, log out everywhere / change the password. Only the admin
    account can see or set this page; the cookie content is never
    shown back, only whether one is saved."""
    u = current_user()
    if not u or not u["is_admin"]:
        return redirect(url_for("login"))
    saved = False
    try:
        con = web_db()
        saved = bool(con.execute("SELECT content FROM web_cookies LIMIT 1").fetchone())
        con.close()
    except Exception:
        pass
    if request.method == "POST":
        content = ""
        if "file" in request.files and request.files["file"].filename:
            content = request.files["file"].read().decode(errors="ignore")
        elif request.form.get("content"):
            content = request.form["content"]
        if not content.strip() or "youtube" not in content.lower():
            return render_template("cookies.html", **ctx(saved=saved, error="Cookies file-ta thik na — YouTube cookies.txt (Netscape format) dao.")), 400
        con = web_db()
        from datetime import datetime as _dt
        con.execute("DELETE FROM web_cookies")
        con.execute("INSERT INTO web_cookies(content, updated) VALUES(?, ?)", (content, _dt.now().isoformat()))
        con.commit()
        con.close()
        return render_template("cookies.html", **ctx(saved=True, ok="Cookies save hoyeche. Ekhon YouTube link abar try koro."))
    return render_template("cookies.html", **ctx(saved=saved))


@app.route("/admin")
def admin():
    u = current_user()
    if not u or not u["is_admin"]:
        return redirect(url_for("login"))
    con = web_db()
    users = con.execute("SELECT * FROM web_users ORDER BY id").fetchall()
    web_dl = con.execute("SELECT COUNT(*) FROM web_history").fetchone()[0]
    con.close()
    bu, _, _ = bot_stats()
    return render_template("admin.html", **ctx(stats={"web_users": len(users), "bot_users": bu, "web_downloads": web_dl}, users=users))


if __name__ == "__main__":
    os.makedirs(WEB_DL_DIR, exist_ok=True)
    web_db().close()
    app.run(host="127.0.0.1", port=8000)
