import os
import sqlite3
import uuid
import secrets
import hmac

from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# ==========================================
# APPLICATION CONFIGURATION
# ==========================================

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "TAWASSOLI_SECRET_KEY",
    "dev-only-change-this-key-before-deployment"
)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "tawasolli.db"

UPLOAD_FOLDER = BASE_DIR / "static" / "uploads" / "posts"
PROFILE_FOLDER = BASE_DIR / "static" / "uploads" / "profiles"
STORY_FOLDER = BASE_DIR / "static" / "uploads" / "stories"
# مجلد صور الشات
CHAT_FOLDER = BASE_DIR / "static" / "uploads" / "chat"

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
PROFILE_FOLDER.mkdir(parents=True, exist_ok=True)
STORY_FOLDER.mkdir(parents=True, exist_ok=True)
CHAT_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_STORY_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "mp4", "webm"
}

# حالة "يكتب الآن"
typing_users = {}


# ==========================================
# DATABASE
# ==========================================

def get_db():
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ==========================================
        # NOTIFICATION PREFERENCES
        # ==========================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INTEGER PRIMARY KEY,
                likes_enabled INTEGER NOT NULL DEFAULT 1,
                comments_enabled INTEGER NOT NULL DEFAULT 1,
                friend_requests_enabled INTEGER NOT NULL DEFAULT 1,
                messages_enabled INTEGER NOT NULL DEFAULT 1,

                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            INSERT OR IGNORE INTO notification_preferences (user_id)
            SELECT id FROM users
        """)

        columns = [
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        ]
        if "is_private" not in columns:
            db.execute("""
                ALTER TABLE users
                ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                media_filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        }

        if "display_name" not in columns:
            db.execute("""
                ALTER TABLE users
                ADD COLUMN display_name TEXT NOT NULL DEFAULT ''
            """)

        if "bio" not in columns:
            db.execute("""
                ALTER TABLE users
                ADD COLUMN bio TEXT NOT NULL DEFAULT ''
            """)

        if "profile_picture" not in columns:
            db.execute("""
                ALTER TABLE users
                ADD COLUMN profile_picture TEXT
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS friendships (
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, friend_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (friend_id) REFERENCES users(id),
                CHECK (user_id != friend_id)
            )
        """)

        # ==========================================
        # BLOCKED USERS
        # ==========================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                blocker_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (blocker_id, blocked_id),

                FOREIGN KEY (blocker_id)
                    REFERENCES users(id) ON DELETE CASCADE,

                FOREIGN KEY (blocked_id)
                    REFERENCES users(id) ON DELETE CASCADE,

                CHECK (blocker_id != blocked_id)
            )
        """)

        # جدول الرسائل مع إمكانية معرفة التوقيت وحالة الـ Vu والصور
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                image_filename TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read INTEGER NOT NULL DEFAULT 0,
                read_at TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id),
                FOREIGN KEY (receiver_id) REFERENCES users(id)
            )
        """)

        # ترقية الجدول الحالي بدون ضياع الرسائل القديمة
        msg_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "is_read" not in msg_columns:
            db.execute("""
                ALTER TABLE messages
                ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0
            """)
        if "read_at" not in msg_columns:
            db.execute("""
                ALTER TABLE messages
                ADD COLUMN read_at TIMESTAMP
            """)
        if "image_filename" not in msg_columns:
            db.execute("""
                ALTER TABLE messages
                ADD COLUMN image_filename TEXT
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS friend_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id),
                FOREIGN KEY (receiver_id) REFERENCES users(id),
                CHECK (sender_id != receiver_id),
                CHECK (status IN ('pending', 'accepted', 'rejected'))
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                actor_id INTEGER,
                notification_type TEXT NOT NULL,
                message TEXT NOT NULL,
                link TEXT NOT NULL DEFAULT '/home',
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (actor_id) REFERENCES users(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                image_filename TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS post_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, user_id),
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS post_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(sender_id, receiver_id, id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_read_status
            ON messages(receiver_id, is_read)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_friend_requests_receiver
            ON friend_requests(receiver_id, status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_user_read
            ON notifications(user_id, is_read, id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_posts_created
            ON posts(id DESC)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_post_likes_post
            ON post_likes(post_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_post_comments_post
            ON post_comments(post_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_stories_user_expiry
            ON stories(user_id, expires_at)
        """)

        # ==========================================
        # ACCOUNT SESSIONS
        # ==========================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_token TEXT NOT NULL UNIQUE,
                device_name TEXT NOT NULL DEFAULT 'Unknown device',
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user
            ON user_sessions(user_id)
        """)

        # ==========================================
        # LOGIN ATTEMPTS
        # ==========================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                successful INTEGER NOT NULL DEFAULT 0
            )
        """)


# ==========================================
# SESSION SECURITY
# ==========================================

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)


@app.before_request
def validate_user_session():

    user_id = session.get("user_id")
    token = session.get("session_token")

    if not user_id:
        return

    if not token:
        session.clear()
        return redirect(url_for("index"))

    with get_db() as db:

        active_session = db.execute("""
            SELECT id
            FROM user_sessions
            WHERE user_id = ?
            AND session_token = ?
            AND expires_at > ?
        """, (
            user_id,
            token,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )).fetchone()

        if active_session is None:
            session.clear()
            flash(
                "Votre session a expiré. Veuillez vous reconnecter.",
                "error"
            )
            return redirect(url_for("index"))

        db.execute("""
            UPDATE user_sessions
            SET last_seen = ?
            WHERE id = ?
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            active_session["id"]
        ))


# ==========================================
# CSRF SECURITY
# ==========================================

def generate_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)

    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = generate_csrf_token


def verify_csrf():
    token = request.form.get("csrf_token", "")
    saved_token = session.get("csrf_token", "")

    return (
        bool(token)
        and bool(saved_token)
        and hmac.compare_digest(token, saved_token)
    )


@app.before_request
def protect_sensitive_requests():

    # حماية طلبات POST
    if request.method != "POST":
        return None

    json_payload = request.get_json(silent=True) or {} if request.is_json else {}

    # التحقق من CSRF في الفورم، أو الهيدرز، أو داخل JSON
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or json_payload.get("csrf_token")
        or ""
    )

    saved_token = session.get("csrf_token", "")

    if not token or not saved_token:
        return jsonify({"error": "Requête refusée : jeton CSRF manquant."}) if request.is_json else ("Requête refusée : jeton CSRF manquant.", 403)

    if not hmac.compare_digest(token, saved_token):
        return jsonify({"error": "Requête refusée : jeton CSRF invalide."}) if request.is_json else ("Requête refusée : jeton CSRF invalide.", 403)

    return None


# ==========================================
# HELPERS
# ==========================================

def create_notification(
    db, user_id, actor_id, notification_type,
    message, link="/home"
):
    db.execute("""
        INSERT INTO notifications (
            user_id, actor_id, notification_type, message, link
        )
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, actor_id, notification_type, message, link))


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    with get_db() as db:
        return db.execute("""
            SELECT id, username, email, display_name,
                   bio, profile_picture, created_at, is_private
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()


def are_friends(user_id, friend_id):
    with get_db() as db:
        result = db.execute("""
            SELECT 1 FROM friendships
            WHERE user_id = ? AND friend_id = ?
        """, (user_id, friend_id)).fetchone()

    return result is not None


def allowed_image(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def allowed_story(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_STORY_EXTENSIONS
    )


def cleanup_expired_stories():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as db:
        expired = db.execute("""
            SELECT id, media_filename
            FROM stories
            WHERE expires_at <= ?
        """, (now,)).fetchall()

        for story in expired:
            path = STORY_FOLDER / story["media_filename"]

            if path.exists():
                path.unlink()

        db.execute("""
            DELETE FROM stories
            WHERE expires_at <= ?
        """, (now,))


def get_stories(user_id):
    cleanup_expired_stories()

    with get_db() as db:
        return db.execute("""
            SELECT
                s.id,
                s.user_id,
                s.media_filename,
                s.media_type,
                s.created_at,
                s.expires_at,
                u.username,
                u.display_name,
                u.profile_picture
            FROM stories s
            JOIN users u ON u.id = s.user_id
            WHERE
                s.user_id = ?
                OR s.user_id IN (
                    SELECT friend_id
                    FROM friendships
                    WHERE user_id = ?
                )
            ORDER BY s.id DESC
        """, (user_id, user_id)).fetchall()


def get_posts(user_id):
    with get_db() as db:

        post_rows = db.execute("""
            SELECT
                p.id,
                p.user_id,
                p.content,
                p.image_filename,
                p.created_at,
                u.username,
                u.display_name,
                u.profile_picture,
                (
                    SELECT COUNT(*)
                    FROM post_likes pl
                    WHERE pl.post_id = p.id
                ) AS likes_count,
                (
                    SELECT COUNT(*)
                    FROM post_comments pc
                    WHERE pc.post_id = p.id
                ) AS comments_count,
                EXISTS(
                    SELECT 1
                    FROM post_likes pl
                    WHERE pl.post_id = p.id AND pl.user_id = ?
                ) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE
                u.id = ?
                OR u.is_private = 0
                OR EXISTS (
                    SELECT 1
                    FROM friendships f
                    WHERE f.user_id = ? AND f.friend_id = u.id
                )
            ORDER BY p.id DESC
            LIMIT 50
        """, (user_id, user_id, user_id)).fetchall()

        posts = []

        for row in post_rows:
            post = dict(row)

            comments = db.execute("""
                SELECT
                    pc.id, pc.content, pc.created_at,
                    pc.user_id, u.username, u.display_name,
                    u.profile_picture
                FROM post_comments pc
                JOIN users u ON u.id = pc.user_id
                WHERE pc.post_id = ?
                ORDER BY pc.id ASC
                LIMIT 100
            """, (post["id"],)).fetchall()

            post["comments"] = [dict(c) for c in comments]
            posts.append(post)

    return posts


def get_conversation_messages(user_id, friend_id, after_id=0):
    with get_db() as db:
        return db.execute("""
            SELECT
                m.id, m.sender_id, m.receiver_id,
                m.content, m.image_filename, m.created_at, m.is_read, m.read_at,
                u.username AS sender_name
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE (
                (m.sender_id = ? AND m.receiver_id = ?)
                OR
                (m.sender_id = ? AND m.receiver_id = ?)
            )
            AND m.id > ?
            ORDER BY m.id ASC
            LIMIT 200
        """, (
            user_id, friend_id,
            friend_id, user_id,
            after_id
        )).fetchall()


# ==========================================
# REGISTER
# ==========================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Veuillez remplir tous les champs.", "error")
            return redirect(url_for("register"))

        if len(username) > 30:
            flash("Le nom doit contenir au maximum 30 caractères.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Les mots de passe ne correspondent pas.", "error")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        try:
            with get_db() as db:
                db.execute("""
                    INSERT INTO users (username, email, password_hash)
                    VALUES (?, ?, ?)
                """, (username, email, password_hash))

        except sqlite3.IntegrityError:
            flash("Cette adresse e-mail est déjà utilisée.", "error")
            return redirect(url_for("register"))

        flash("Compte créé avec succès ! Connectez-vous.", "success")
        return redirect(url_for("login_page"))

    return render_template("register.html")


# ==========================================
# LOGIN
# ==========================================

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("home"))

    return render_template("splash.html")


@app.route("/login-page")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    ip_address = request.remote_addr or "unknown"

    with get_db() as db:

        recent_attempts = db.execute("""
            SELECT COUNT(*) AS total
            FROM login_attempts
            WHERE email = ?
            AND ip_address = ?
            AND successful = 0
            AND attempted_at >= datetime('now', '-15 minutes')
        """, (email, ip_address)).fetchone()["total"]

        if recent_attempts >= 5:
            flash(
                "Trop de tentatives. Réessayez dans 15 minutes.",
                "error"
            )
            return redirect(url_for("login_page"))

        user = db.execute("""
            SELECT *
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

        if user is None or not check_password_hash(
            user["password_hash"], password
        ):

            db.execute("""
                INSERT INTO login_attempts (
                    email, ip_address, successful
                )
                VALUES (?, ?, 0)
            """, (email, ip_address))

            flash("E-mail ou mot de passe incorrect.", "error")
            return redirect(url_for("login_page"))

        # Successful login
        db.execute("""
            INSERT INTO login_attempts (
                email, ip_address, successful
            )
            VALUES (?, ?, 1)
        """, (email, ip_address))

        token = uuid.uuid4().hex
        device = request.user_agent.string[:200] or "Unknown device"
        expires = datetime.now() + timedelta(days=7)

        db.execute("""
            INSERT INTO user_sessions (
                user_id,
                session_token,
                device_name,
                ip_address,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user["id"],
            token,
            device,
            ip_address,
            expires.strftime("%Y-%m-%d %H:%M:%S")
        ))

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["session_token"] = token

    return redirect(url_for("home"))


# ==========================================
# HOME (مع احتساب الشارة الحمراء للرسائل)
# ==========================================

@app.route("/home")
def home():

    user = current_user()

    if user is None:
        session.clear()
        return redirect(url_for("index"))

    with get_db() as db:

        friends = db.execute("""
            SELECT u.id, u.username, u.display_name, u.profile_picture
            FROM friendships f
            JOIN users u ON u.id = f.friend_id
            WHERE f.user_id = ?
            ORDER BY u.username COLLATE NOCASE
        """, (user["id"],)).fetchall()

        incoming_requests = db.execute("""
            SELECT
                fr.id AS request_id,
                fr.created_at,
                u.id AS sender_id,
                u.username AS sender_username
            FROM friend_requests fr
            JOIN users u ON u.id = fr.sender_id
            WHERE fr.receiver_id = ? AND fr.status = 'pending'
            ORDER BY fr.id DESC
        """, (user["id"],)).fetchall()

        outgoing_requests = db.execute("""
            SELECT
                fr.id AS request_id,
                fr.created_at,
                u.id AS receiver_id,
                u.username AS receiver_username
            FROM friend_requests fr
            JOIN users u ON u.id = fr.receiver_id
            WHERE fr.sender_id = ? AND fr.status = 'pending'
            ORDER BY fr.id DESC
        """, (user["id"],)).fetchall()

        notifications = db.execute("""
            SELECT id, actor_id, notification_type, message,
                   link, is_read, created_at
            FROM notifications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user["id"],)).fetchall()

        unread_notifications = db.execute("""
            SELECT COUNT(*) AS total
            FROM notifications
            WHERE user_id = ? AND is_read = 0
        """, (user["id"],)).fetchone()["total"]

        # حساب عدد الرسائل غير المقروءة للشارة الحمراء
        unread_messages_count = db.execute("""
            SELECT COUNT(*) AS total
            FROM messages
            WHERE receiver_id = ? AND is_read = 0
        """, (user["id"],)).fetchone()["total"]

    posts = get_posts(user["id"])
    stories = get_stories(user["id"])

    return render_template(
        "home.html",
        user=user,
        friends=friends,
        incoming_requests=incoming_requests,
        outgoing_requests=outgoing_requests,
        notifications=notifications,
        unread_notifications=unread_notifications,
        unread_messages_count=unread_messages_count,
        posts=posts,
        stories=stories
    )


# ==========================================
# STORIES
# ==========================================

@app.route("/stories/create", methods=["POST"])
def create_story():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    media = request.files.get("media")

    if not media or not media.filename:
        flash("Choisis une photo ou une vidéo.", "error")
        return redirect(url_for("home"))

    if not allowed_story(media.filename):
        flash("Format non autorisé. Utilise JPG, PNG, GIF, WEBP, MP4 ou WEBM.", "error")
        return redirect(url_for("home"))

    extension = secure_filename(media.filename).rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"

    media_type = "video" if extension in {"mp4", "webm"} else "image"

    media.save(STORY_FOLDER / filename)

    now = datetime.now()
    expires = now + timedelta(hours=24)

    with get_db() as db:
        db.execute("""
            INSERT INTO stories (
                user_id, media_filename, media_type, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user["id"],
            filename,
            media_type,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S")
        ))

    flash("Story publiée avec succès !", "success")
    return redirect(url_for("home"))


@app.route("/stories/<int:story_id>")
def view_story(story_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    cleanup_expired_stories()

    with get_db() as db:
        story = db.execute("""
            SELECT
                s.*,
                u.username,
                u.display_name,
                u.profile_picture
            FROM stories s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
        """, (story_id,)).fetchone()

    if story is None:
        flash("Cette Story n'existe plus.", "error")
        return redirect(url_for("home"))

    if story["user_id"] != user["id"] and not are_friends(
        user["id"], story["user_id"]
    ):
        flash("Tu ne peux pas voir cette Story.", "error")
        return redirect(url_for("home"))

    return render_template(
        "story.html",
        user=user,
        story=story
    )


@app.route("/stories/<int:story_id>/delete", methods=["POST"])
def delete_story(story_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:
        story = db.execute("""
            SELECT id, user_id, media_filename
            FROM stories
            WHERE id = ?
        """, (story_id,)).fetchone()

        if story is None:
            flash("Cette Story n'existe pas.", "error")
            return redirect(url_for("home"))

        if story["user_id"] != user["id"]:
            flash("Tu ne peux supprimer que tes propres Stories.", "error")
            return redirect(url_for("home"))

        db.execute(
            "DELETE FROM stories WHERE id = ?",
            (story_id,)
        )

    path = STORY_FOLDER / story["media_filename"]

    if path.exists():
        path.unlink()

    flash("Story supprimée.", "success")
    return redirect(url_for("home"))


# ==========================================
# CREATE POST
# ==========================================

@app.route("/posts/create", methods=["POST"])
def create_post():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    content = request.form.get("content", "").strip()
    image = request.files.get("image")
    image_filename = None

    if len(content) > 2000:
        flash("Le texte dépasse 2000 caractères.", "error")
        return redirect(url_for("home"))

    if image and image.filename:

        if not allowed_image(image.filename):
            flash("Format d'image non autorisé.", "error")
            return redirect(url_for("home"))

        extension = secure_filename(image.filename).rsplit(".", 1)[1].lower()
        image_filename = f"{uuid.uuid4().hex}.{extension}"

        image.save(UPLOAD_FOLDER / image_filename)

    if not content and not image_filename:
        flash("Écris un texte ou ajoute une image.", "error")
        return redirect(url_for("home"))

    with get_db() as db:
        db.execute("""
            INSERT INTO posts (user_id, content, image_filename)
            VALUES (?, ?, ?)
        """, (user["id"], content, image_filename))

    flash("Publication ajoutée avec succès !", "success")
    return redirect(url_for("home"))


# ==========================================
# LIKE / UNLIKE POST
# ==========================================

@app.route("/posts/<int:post_id>/like", methods=["POST"])
def like_post(post_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        post = db.execute("""
            SELECT id, user_id FROM posts WHERE id = ?
        """, (post_id,)).fetchone()

        if post is None:
            flash("Cette publication n'existe pas.", "error")
            return redirect(url_for("home"))

        existing_like = db.execute("""
            SELECT id FROM post_likes
            WHERE post_id = ? AND user_id = ?
        """, (post_id, user["id"])).fetchone()

        if existing_like:
            db.execute("""
                DELETE FROM post_likes
                WHERE post_id = ? AND user_id = ?
            """, (post_id, user["id"]))

        else:
            db.execute("""
                INSERT INTO post_likes (post_id, user_id)
                VALUES (?, ?)
            """, (post_id, user["id"]))

            if post["user_id"] != user["id"]:
                create_notification(
                    db, post["user_id"], user["id"],
                    "post_like",
                    f"{user['username']} a aimé ta publication.",
                    "/home"
                )

    return redirect(url_for("home"))


# ==========================================
# ADD COMMENT
# ==========================================

@app.route("/posts/<int:post_id>/comment", methods=["POST"])
def add_comment(post_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    content = request.form.get("content", "").strip()

    if not content:
        flash("Le commentaire est vide.", "error")
        return redirect(url_for("home"))

    if len(content) > 1000:
        flash("Le commentaire dépasse 1000 caractères.", "error")
        return redirect(url_for("home"))

    with get_db() as db:

        post = db.execute("""
            SELECT id, user_id FROM posts WHERE id = ?
        """, (post_id,)).fetchone()

        if post is None:
            flash("Cette publication n'existe pas.", "error")
            return redirect(url_for("home"))

        db.execute("""
            INSERT INTO post_comments (post_id, user_id, content)
            VALUES (?, ?, ?)
        """, (post_id, user["id"], content))

        if post["user_id"] != user["id"]:
            create_notification(
                db, post["user_id"], user["id"],
                "post_comment",
                f"{user['username']} a commenté ta publication.",
                "/home"
            )

    flash("Commentaire ajouté.", "success")
    return redirect(url_for("home"))


# ==========================================
# DELETE POST
# ==========================================

@app.route("/posts/<int:post_id>/delete", methods=["POST"])
def delete_post(post_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        post = db.execute("""
            SELECT id, user_id, image_filename
            FROM posts WHERE id = ?
        """, (post_id,)).fetchone()

        if post is None:
            flash("Cette publication n'existe pas.", "error")
            return redirect(url_for("home"))

        if post["user_id"] != user["id"]:
            flash("Tu ne peux supprimer que tes propres publications.", "error")
            return redirect(url_for("home"))

        image_filename = post["image_filename"]

        db.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    if image_filename:
        image_path = UPLOAD_FOLDER / image_filename
        if image_path.exists():
            image_path.unlink()

    flash("Publication supprimée.", "success")
    return redirect(url_for("home"))


# ==========================================
# PROFILE - OWN PROFILE
# ==========================================

@app.route("/profile")
def my_profile():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    return redirect(url_for("profile", user_id=user["id"]))


# ==========================================
# PROFILE - VIEW USER (مع دعم البلوك)
# ==========================================

@app.route("/profile/<int:user_id>")
def profile(user_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        profile_user = db.execute("""
            SELECT
                id, username, email, display_name,
                bio, profile_picture, created_at, is_private
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        if profile_user is None:
            flash("Utilisateur introuvable.", "error")
            return redirect(url_for("home"))

        # فحص الحظر بيني وبينه
        is_blocked_by_me = False
        if user["id"] != user_id:
            is_blocked_by_me = db.execute("""
                SELECT 1 FROM blocked_users
                WHERE blocker_id = ? AND blocked_id = ?
            """, (user["id"], user_id)).fetchone() is not None

        if (
            profile_user["is_private"]
            and profile_user["id"] != user["id"]
            and not are_friends(user["id"], profile_user["id"])
            and not is_blocked_by_me
        ):
            flash("Ce compte est privé. Tu dois être ami(e) avec cet utilisateur.", "error")
            return redirect(url_for("home"))

        posts_rows = db.execute("""
            SELECT
                p.id,
                p.user_id,
                p.content,
                p.image_filename,
                p.created_at,
                (
                    SELECT COUNT(*)
                    FROM post_likes
                    WHERE post_id = p.id
                ) AS likes_count,
                (
                    SELECT COUNT(*)
                    FROM post_comments
                    WHERE post_id = p.id
                ) AS comments_count,
                EXISTS(
                    SELECT 1 FROM post_likes
                    WHERE post_id = p.id AND user_id = ?
                ) AS liked_by_me
            FROM posts p
            WHERE p.user_id = ?
            ORDER BY p.id DESC
        """, (user["id"], user_id)).fetchall()

        profile_posts = []

        for row in posts_rows:
            post = dict(row)

            comments = db.execute("""
                SELECT
                    pc.id, pc.content, pc.created_at,
                    pc.user_id, u.username, u.display_name,
                    u.profile_picture
                FROM post_comments pc
                JOIN users u ON u.id = pc.user_id
                WHERE pc.post_id = ?
                ORDER BY pc.id ASC
                LIMIT 100
            """, (post["id"],)).fetchall()

            post["comments"] = [dict(c) for c in comments]
            profile_posts.append(post)

        posts_count = db.execute("""
            SELECT COUNT(*) FROM posts WHERE user_id = ?
        """, (user_id,)).fetchone()[0]

        friends_count = db.execute("""
            SELECT COUNT(*) FROM friendships WHERE user_id = ?
        """, (user_id,)).fetchone()[0]

    return render_template(
        "profile.html",
        user=user,
        profile_user=profile_user,
        posts=profile_posts,
        posts_count=posts_count,
        friends_count=friends_count,
        is_owner=(user["id"] == user_id),
        is_blocked_by_me=is_blocked_by_me
    )


# ==========================================
# EDIT PROFILE
# ==========================================

@app.route("/profile/edit", methods=["GET", "POST"])
def edit_profile():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    if request.method == "POST":

        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()
        image = request.files.get("profile_picture")

        if len(display_name) > 50:
            flash("Le nom doit contenir 50 caractères maximum.", "error")
            return redirect(url_for("edit_profile"))

        if len(bio) > 250:
            flash("La bio doit contenir 250 caractères maximum.", "error")
            return redirect(url_for("edit_profile"))

        profile_picture = user["profile_picture"]

        if image and image.filename:

            if not allowed_image(image.filename):
                flash("Format d'image non autorisé.", "error")
                return redirect(url_for("edit_profile"))

            extension = secure_filename(image.filename).rsplit(".", 1)[1].lower()
            new_filename = f"{uuid.uuid4().hex}.{extension}"

            image.save(PROFILE_FOLDER / new_filename)

            if profile_picture:
                old_path = PROFILE_FOLDER / profile_picture
                if old_path.exists():
                    old_path.unlink()

            profile_picture = new_filename

        with get_db() as db:
            db.execute("""
                UPDATE users
                SET display_name = ?,
                    bio = ?,
                    profile_picture = ?
                WHERE id = ?
            """, (
                display_name,
                bio,
                profile_picture,
                user["id"]
            ))

        flash("Profil modifié avec succès !", "success")
        return redirect(url_for("profile", user_id=user["id"]))

    return render_template("edit_profile.html", user=user)


# ==========================================
# SEARCH USERS
# ==========================================

@app.route("/search-users")
def search_users():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    query = request.args.get("q", "").strip()
    results = []

    if query:
        with get_db() as db:
            results = db.execute("""
                SELECT
                    u.id,
                    u.username,
                    u.display_name,
                    u.profile_picture,
                    u.is_private,

                    EXISTS(
                        SELECT 1
                        FROM friendships f
                        WHERE f.user_id = ?
                        AND f.friend_id = u.id
                    ) AS is_friend,

                    EXISTS(
                        SELECT 1
                        FROM friend_requests fr
                        WHERE fr.sender_id = ?
                        AND fr.receiver_id = u.id
                        AND fr.status = 'pending'
                    ) AS request_sent,

                    EXISTS(
                        SELECT 1
                        FROM friend_requests fr
                        WHERE fr.sender_id = u.id
                        AND fr.receiver_id = ?
                        AND fr.status = 'pending'
                    ) AS request_received

                FROM users u
                WHERE u.id != ?
                AND (
                    u.username LIKE ?
                    OR u.display_name LIKE ?
                )
                ORDER BY u.username COLLATE NOCASE
                LIMIT 30

            """, (
                user["id"],
                user["id"],
                user["id"],
                user["id"],
                f"%{query}%",
                f"%{query}%"
            )).fetchall()

    return render_template(
        "search_users.html",
        user=user,
        query=query,
        results=results
    )


# ==========================================
# FRIEND REQUESTS
# ==========================================

@app.route("/friends/add", methods=["POST"])
def add_friend():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    username = request.form.get("username", "").strip()

    if not username:
        flash("Entre le nom d'utilisateur de ton ami.", "error")
        return redirect(url_for("home"))

    with get_db() as db:

        friend = db.execute("""
            SELECT id, username FROM users
            WHERE username = ? COLLATE NOCASE
        """, (username,)).fetchone()

        if friend is None:
            flash("Aucun utilisateur trouvé avec ce nom.", "error")
            return redirect(url_for("home"))

        if friend["id"] == user["id"]:
            flash("Tu ne peux pas t'envoyer une demande à toi-même.", "error")
            return redirect(url_for("home"))

        existing_friendship = db.execute("""
            SELECT 1 FROM friendships
            WHERE user_id = ? AND friend_id = ?
        """, (user["id"], friend["id"])).fetchone()

        if existing_friendship:
            flash("Cet utilisateur est déjà ton ami.", "error")
            return redirect(url_for("home"))

        existing_request = db.execute("""
            SELECT id, sender_id, receiver_id
            FROM friend_requests
            WHERE status = 'pending'
            AND (
                (sender_id = ? AND receiver_id = ?)
                OR
                (sender_id = ? AND receiver_id = ?)
            )
            LIMIT 1
        """, (
            user["id"], friend["id"],
            friend["id"], user["id"]
        )).fetchone()

        if existing_request:

            if (
                existing_request["sender_id"] == friend["id"]
                and existing_request["receiver_id"] == user["id"]
            ):

                db.execute("""
                    UPDATE friend_requests
                    SET status = 'accepted'
                    WHERE id = ?
                """, (existing_request["id"],))

                db.execute("""
                    INSERT OR IGNORE INTO friendships (user_id, friend_id)
                    VALUES (?, ?)
                """, (user["id"], friend["id"]))

                db.execute("""
                    INSERT OR IGNORE INTO friendships (user_id, friend_id)
                    VALUES (?, ?)
                """, (friend["id"], user["id"]))

                create_notification(
                    db, friend["id"], user["id"],
                    "friend_accepted",
                    f"{user['username']} a accepté ta demande d'ami(e).",
                    "/home"
                )

                flash(
                    f"Tu es maintenant ami(e) avec {friend['username']} !",
                    "success"
                )

            else:
                flash("Une demande est déjà en attente.", "error")

            return redirect(url_for("home"))

        db.execute("""
            INSERT INTO friend_requests (sender_id, receiver_id, status)
            VALUES (?, ?, 'pending')
        """, (user["id"], friend["id"]))

        create_notification(
            db, friend["id"], user["id"],
            "friend_request",
            f"{user['username']} t'a envoyé une demande d'ami(e).",
            "/home"
        )

    flash(f"Demande envoyée à {friend['username']} !", "success")
    return redirect(url_for("home"))


@app.route("/friends/accept/<int:request_id>", methods=["POST"])
def accept_friend_request(request_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        friend_request = db.execute("""
            SELECT id, sender_id, receiver_id, status
            FROM friend_requests WHERE id = ?
        """, (request_id,)).fetchone()

        if friend_request is None:
            flash("Cette demande n'existe pas.", "error")
            return redirect(url_for("home"))

        if friend_request["receiver_id"] != user["id"]:
            flash("Tu n'as pas l'autorisation d'accepter cette demande.", "error")
            return redirect(url_for("home"))

        if friend_request["status"] != "pending":
            flash("Cette demande a déjà été traitée.", "error")
            return redirect(url_for("home"))

        friend_id = friend_request["sender_id"]

        db.execute("""
            INSERT OR IGNORE INTO friendships (user_id, friend_id)
            VALUES (?, ?)
        """, (user["id"], friend_id))

        db.execute("""
            INSERT OR IGNORE INTO friendships (user_id, friend_id)
            VALUES (?, ?)
        """, (friend_id, user["id"]))

        db.execute("""
            UPDATE friend_requests
            SET status = 'accepted'
            WHERE id = ?
        """, (request_id,))

        friend = db.execute("""
            SELECT username FROM users WHERE id = ?
        """, (friend_id,)).fetchone()

        create_notification(
            db, friend_id, user["id"],
            "friend_accepted",
            f"{user['username']} a accepté ta demande d'ami(e).",
            "/home"
        )

    friend_name = friend["username"] if friend else "Utilisateur"
    flash(f"Tu as accepté la demande de {friend_name} !", "success")
    return redirect(url_for("home"))


@app.route("/friends/reject/<int:request_id>", methods=["POST"])
def reject_friend_request(request_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        friend_request = db.execute("""
            SELECT id, receiver_id, status
            FROM friend_requests WHERE id = ?
        """, (request_id,)).fetchone()

        if friend_request is None:
            flash("Cette demande n'existe pas.", "error")
            return redirect(url_for("home"))

        if friend_request["receiver_id"] != user["id"]:
            flash("Tu n'as pas l'autorisation de refuser هذه الطلب.", "error")
            return redirect(url_for("home"))

        if friend_request["status"] != "pending":
            flash("Cette demande a déjà été traitée.", "error")
            return redirect(url_for("home"))

        db.execute("""
            UPDATE friend_requests
            SET status = 'rejected'
            WHERE id = ?
        """, (request_id,))

    flash("Demande de contact refusée.", "success")
    return redirect(url_for("home"))


# ==========================================
# MESSAGES PAGE
# ==========================================

@app.route("/messages")
def messages():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        conversations = db.execute("""
            SELECT
                u.id AS friend_id,
                u.username,
                u.display_name,
                u.profile_picture,
                m.content AS last_message,
                m.image_filename AS last_image,
                m.created_at AS last_message_date,
                m.sender_id AS last_sender_id,
                m.is_read AS last_is_read,
                (
                    SELECT COUNT(*)
                    FROM messages unread
                    WHERE unread.sender_id = u.id
                      AND unread.receiver_id = ?
                      AND unread.is_read = 0
                ) AS unread_count

            FROM friendships f

            JOIN users u
                ON u.id = f.friend_id

            JOIN messages m
                ON m.id = (
                    SELECT m2.id
                    FROM messages m2
                    WHERE
                        (m2.sender_id = ? AND m2.receiver_id = u.id)
                        OR
                        (m2.sender_id = u.id AND m2.receiver_id = ?)
                    ORDER BY m2.id DESC
                    LIMIT 1
                )

            WHERE f.user_id = ?

            ORDER BY m.id DESC

        """, (
            user["id"],
            user["id"],
            user["id"],
            user["id"]
        )).fetchall()

    return render_template(
        "messages.html",
        user=user,
        conversations=conversations
    )


# ==========================================
# PRIVATE CHAT
# ==========================================

@app.route("/chat/<int:friend_id>")
def chat(friend_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    if not are_friends(user["id"], friend_id):
        flash("Tu dois être ami(e) avec cet utilisateur pour discuter.", "error")
        return redirect(url_for("home"))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as db:
        # تحديث الرسائل الواردة غير المقروءة لتصبح مقروءة مع تسجيل توقيت المشاهدة
        db.execute("""
            UPDATE messages
            SET is_read = 1,
                read_at = ?
            WHERE receiver_id = ?
              AND sender_id = ?
              AND is_read = 0
        """, (now_str, user["id"], friend_id))

        friend = db.execute("""
            SELECT id, username, display_name, profile_picture
            FROM users WHERE id = ?
        """, (friend_id,)).fetchone()

    if friend is None:
        return redirect(url_for("home"))

    messages = get_conversation_messages(user["id"], friend_id)

    return render_template(
        "chat.html",
        user=user,
        friend=friend,
        messages=messages
    )


# ==========================================
# BLOCKED ACCOUNTS
# ==========================================

@app.route("/settings/blocked")
def blocked_accounts():

    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("index"))

    with get_db() as db:

        blocked_users = db.execute("""
            SELECT users.*
            FROM blocked_users
            JOIN users
                ON users.id = blocked_users.blocked_id
            WHERE blocked_users.blocker_id = ?
            ORDER BY blocked_users.created_at DESC
        """, (user_id,)).fetchall()

    return render_template(
        "blocked_accounts.html",
        user=current_user(),
        blocked_users=blocked_users
    )


# ==========================================
# BLOCK USER
# ==========================================

@app.route("/users/<int:user_id>/block", methods=["POST"])
def block_user(user_id):

    current_id = session.get("user_id")

    if not current_id:
        return redirect(url_for("index"))

    if current_id == user_id:
        flash(
            "Vous ne pouvez pas bloquer votre propre compte.",
            "error"
        )
        return redirect(url_for("home"))

    with get_db() as db:

        target = db.execute("""
            SELECT id
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        if target is None:
            flash("Utilisateur introuvable.", "error")
            return redirect(url_for("home"))

        # Ajouter le compte à la liste des comptes bloqués
        db.execute("""
            INSERT OR IGNORE INTO blocked_users
            (blocker_id, blocked_id)
            VALUES (?, ?)
        """, (current_id, user_id))

        # Supprimer l'amitié
        db.execute("""
            DELETE FROM friendships
            WHERE
                (user_id = ? AND friend_id = ?)
                OR
                (user_id = ? AND friend_id = ?)
        """, (current_id, user_id, user_id, current_id))

        # Supprimer les demandes d'amitié en attente
        db.execute("""
            DELETE FROM friend_requests
            WHERE status = 'pending'
            AND (
                (sender_id = ? AND receiver_id = ?)
                OR
                (sender_id = ? AND receiver_id = ?)
            )
        """, (current_id, user_id, user_id, current_id))

    flash("Cet utilisateur a été bloqué.", "success")
    return redirect(url_for("blocked_accounts"))


# ==========================================
# UNBLOCK USER
# ==========================================

@app.route("/users/<int:user_id>/unblock", methods=["POST"])
def unblock_user(user_id):

    current_id = session.get("user_id")

    if not current_id:
        return redirect(url_for("index"))

    with get_db() as db:

        db.execute("""
            DELETE FROM blocked_users
            WHERE blocker_id = ?
            AND blocked_id = ?
        """, (current_id, user_id))

    flash("Utilisateur débloqué avec succès.", "success")
    return redirect(url_for("blocked_accounts"))


# ==========================================
# TYPING STATUS API (يكتب الآن)
# ==========================================

@app.route("/api/typing/<int:friend_id>", methods=["POST"])
def set_typing(friend_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Connexion requise."}), 401

    typing_users[(user["id"], friend_id)] = datetime.now()
    return jsonify({"status": "ok"})


# ==========================================
# SEND AND RECEIVE MESSAGES & PHOTOS (API)
# ==========================================

@app.route("/api/messages/<int:friend_id>", methods=["GET", "POST"])
def messages_api(friend_id):

    user = current_user()

    if user is None:
        return jsonify({"error": "Connexion requise."}), 401

    if not are_friends(user["id"], friend_id):
        return jsonify({"error": "Accès refusé."}), 403

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # إرسال رسالة (نص أو صورة أو كلاهما)
    if request.method == "POST":

        content = ""
        image_filename = None

        if request.is_json:
            data = request.get_json(silent=True) or {}
            content = str(data.get("content", "")).strip()
        else:
            content = str(request.form.get("content", "")).strip()
            image = request.files.get("image")
            if image and image.filename and allowed_image(image.filename):
                ext = secure_filename(image.filename).rsplit(".", 1)[1].lower()
                image_filename = f"{uuid.uuid4().hex}.{ext}"
                image.save(CHAT_FOLDER / image_filename)

        if not content and not image_filename:
            return jsonify({"error": "Le message est vide."}), 400

        if len(content) > 2000:
            return jsonify({"error": "Le message dépasse 2000 caractères."}), 400

        with get_db() as db:

            result = db.execute("""
                INSERT INTO messages (sender_id, receiver_id, content, image_filename, created_at, is_read)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (user["id"], friend_id, content, image_filename, now_str))

            message_id = result.lastrowid

            message = db.execute("""
                SELECT
                    m.id, m.sender_id, m.receiver_id,
                    m.content, m.image_filename, m.created_at, m.is_read, m.read_at,
                    u.username AS sender_name
                FROM messages m
                JOIN users u ON u.id = m.sender_id
                WHERE m.id = ?
            """, (message_id,)).fetchone()

            # مسح حالة "يكتب الآن" عند الإرسال
            typing_users.pop((user["id"], friend_id), None)

            create_notification(
                db, friend_id, user["id"],
                "message",
                f"{user['username']} t'a envoyé un message.",
                f"/chat/{user['id']}"
            )

        return jsonify(dict(message)), 201

    # استرجاع الرسائل (GET) وتحديث الرسائل الواردة لـ "مقروءة" مع التوقيت
    with get_db() as db:
        db.execute("""
            UPDATE messages
            SET is_read = 1,
                read_at = ?
            WHERE receiver_id = ?
              AND sender_id = ?
              AND is_read = 0
        """, (now_str, user["id"], friend_id))

    try:
        after_id = max(0, int(request.args.get("after", "0")))
    except (ValueError, TypeError):
        return jsonify({"error": "Identifiant invalide."}), 400

    messages = get_conversation_messages(
        user["id"], friend_id, after_id
    )

    # فحص هل الطرف الآخر يكتب الآن
    last_typed = typing_users.get((friend_id, user["id"]))
    is_typing = False
    if last_typed and (datetime.now() - last_typed).total_seconds() < 4:
        is_typing = True

    return jsonify({
        "messages": [dict(message) for message in messages],
        "is_typing": is_typing,
        "server_time": now_str
    })


# ==========================================
# NOTIFICATIONS
# ==========================================

@app.route("/notifications/read/<int:notification_id>", methods=["POST"])
def mark_notification_read(notification_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:
        db.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = ? AND user_id = ?
        """, (notification_id, user["id"]))

    return redirect(url_for("home"))


@app.route("/notifications/read-all", methods=["POST"])
def mark_all_notifications_read():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:
        db.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE user_id = ? AND is_read = 0
        """, (user["id"],))

    flash("Toutes les notifications ont été marquées comme lues.", "success")
    return redirect(url_for("home"))


@app.route("/api/notifications")
def notifications_api():

    user = current_user()

    if user is None:
        return jsonify({"error": "Connexion requise."}), 401

    with get_db() as db:

        rows = db.execute("""
            SELECT id, notification_type, message, link,
                   is_read, created_at
            FROM notifications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user["id"],)).fetchall()

        unread = db.execute("""
            SELECT COUNT(*) AS total
            FROM notifications
            WHERE user_id = ? AND is_read = 0
        """, (user["id"],)).fetchone()["total"]

    return jsonify({
        "notifications": [dict(row) for row in rows],
        "unread_count": unread
    })


# ==========================================
# SETTINGS
# ==========================================

@app.route("/settings")
def settings():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    return render_template("settings.html", user=user)


# ==========================================
# CHANGE PASSWORD
# ==========================================

@app.route("/settings/change-password", methods=["GET", "POST"])
def change_password():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    if request.method == "POST":

        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        with get_db() as db:

            account = db.execute("""
                SELECT password_hash
                FROM users
                WHERE id = ?
            """, (user["id"],)).fetchone()

            if account is None or not check_password_hash(
                account["password_hash"], current_password
            ):
                flash("Mot de passe actuel incorrect.", "error")
                return redirect(url_for("change_password"))

            if len(new_password) < 8:
                flash(
                    "Le nouveau mot de passe doit contenir au moins 8 caractères.",
                    "error"
                )
                return redirect(url_for("change_password"))

            if new_password != confirm_password:
                flash(
                    "Les nouveaux mots de passe ne correspondent pas.",
                    "error"
                )
                return redirect(url_for("change_password"))

            if check_password_hash(account["password_hash"], new_password):
                flash(
                    "Choisis un mot de passe différent de l'ancien.",
                    "error"
                )
                return redirect(url_for("change_password"))

            # 1. Changer le mot de passe
            new_hash = generate_password_hash(new_password)

            db.execute("""
                UPDATE users
                SET password_hash = ?
                WHERE id = ?
            """, (new_hash, user["id"]))

            # 2. Créer une nouvelle session pour cet appareil
            new_token = uuid.uuid4().hex

            expires_at = (
               datetime.now() + timedelta(days=7)
            ).strftime("%Y-%m-%d %H:%M:%S")

            device_name = request.headers.get(
                "User-Agent", "Unknown device"
            )

            ip_address = request.remote_addr or "Unknown"

            # 3. Supprimer toutes les anciennes sessions
            db.execute("""
                DELETE FROM user_sessions
                WHERE user_id = ?
            """, (user["id"],))

            # 4. Enregistrer la nouvelle session
            db.execute("""
                INSERT INTO user_sessions (
                    user_id,
                    session_token,
                    device_name,
                    ip_address,
                    created_at,
                    last_seen,
                    expires_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, ?)
            """, (
                user["id"],
                new_token,
                device_name,
                ip_address,
                expires_at
            ))

        # 5. Actualiser la session Flask actuelle
        session["session_token"] = new_token
        session.permanent = True

        flash(
            "Mot de passe modifié avec succès ! Les autres appareils ont été déconnectés.",
            "success"
        )

        return redirect(url_for("account_security"))

    return render_template(
        "change_password.html",
        user=user
    )


# ==========================================
# ACCOUNT SECURITY
# ==========================================

@app.route("/settings/security")
def account_security():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    current_token = session.get("session_token")

    with get_db() as db:

        sessions = db.execute("""
            SELECT
                id,
                device_name,
                ip_address,
                created_at,
                last_seen,
                session_token
            FROM user_sessions
            WHERE user_id = ?
            AND expires_at > ?
            ORDER BY last_seen DESC
        """, (
            user["id"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )).fetchall()

    return render_template(
        "security.html",
        user=user,
        sessions=sessions,
        current_token=current_token
    )


@app.route("/settings/security/logout-all", methods=["POST"])
def logout_all_sessions():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:
        db.execute("""
            DELETE FROM user_sessions
            WHERE user_id = ?
        """, (user["id"],))

    session.clear()

    flash(
        "Toutes les sessions ont été déconnectées.",
        "success"
    )

    return redirect(url_for("index"))


@app.route(
    "/settings/security/logout-session/<int:session_id>",
    methods=["POST"]
)
def logout_one_session(session_id):

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    with get_db() as db:

        target = db.execute("""
            SELECT session_token
            FROM user_sessions
            WHERE id = ? AND user_id = ?
        """, (session_id, user["id"])).fetchone()

        if target is None:
            flash("Session introuvable.", "error")
            return redirect(url_for("account_security"))

        if target["session_token"] == session.get("session_token"):
            flash(
                "Utilisez la déconnexion normale pour fermer cette session.",
                "error"
            )
            return redirect(url_for("account_security"))

        db.execute("""
            DELETE FROM user_sessions
            WHERE id = ? AND user_id = ?
        """, (session_id, user["id"]))

    flash("Session déconnectée.", "success")
    return redirect(url_for("account_security"))


# ==========================================
# ACCOUNT PRIVACY
# ==========================================

@app.route("/settings/privacy", methods=["GET", "POST"])
def account_privacy():

    user = current_user()

    if user is None:
        return redirect(url_for("index"))

    if request.method == "POST":

        privacy = request.form.get("privacy")

        if privacy not in ("public", "private"):
            flash("Choix invalide.", "error")
            return redirect(url_for("account_privacy"))

        is_private = 1 if privacy == "private" else 0

        with get_db() as db:
            db.execute("""
                UPDATE users
                SET is_private = ?
                WHERE id = ?
            """, (is_private, user["id"]))

        flash("Paramètres de confidentialité enregistrés !", "success")

        return redirect(url_for("account_privacy"))

    return render_template(
        "privacy.html",
        user=user,
        is_private=user["is_private"]
    )


# ==========================================
# LOGOUT
# ==========================================

@app.route("/logout")
def logout():

    token = session.get("session_token")

    if token:
        with get_db() as db:
            db.execute("""
                DELETE FROM user_sessions
                WHERE session_token = ?
            """, (token,))

    session.clear()

    return redirect(url_for("index"))


# ==========================================
# ERROR HANDLER
# ==========================================

@app.errorhandler(413)
def file_too_large(error):
    flash("Le fichier est trop grand. Taille maximale : 50 MB.", "error")
    return redirect(url_for("home"))


# ==========================================
# START APPLICATION
# ==========================================

init_db()

if __name__ == "__main__":
    app.run(debug=True)