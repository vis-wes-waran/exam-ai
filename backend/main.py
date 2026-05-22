from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3, hashlib, secrets, json, os, smtplib, logging, hashlib as _hs
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta
from groq import Groq
from typing import Optional, List
import pytz
import random

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

try:
    import requests as _req_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    _req_lib = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("examai")

# ── CONFIG ────────────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY missing!")

client = Groq(api_key=GROQ_API_KEY)

MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
APP_URL   = os.getenv("APP_URL", "http://localhost:8000")
TZ_NAME   = os.getenv("TIMEZONE", "Asia/Kolkata")
TZ        = pytz.timezone(TZ_NAME)

DB_PATH                   = "examai.db"
UNLOCK_REQUIRED_ATTEMPTS  = 20
UNLOCK_REQUIRED_ACCURACY  = 70
REQUIRED_SUB_ATTEMPTS     = 5
MAX_Q_PER_SUBCHAPTER      = 20
SUBCHAPTERS_PER_CHAPTER   = 5
DYNAMIC_MAX_PER_CALL      = 4
COACH_MEMORY_LIMIT        = 30
DAILY_CHALLENGE_XP        = 75
COINS_PER_CORRECT         = 2
HINT_COST_COINS           = 5
FIFTY_FIFTY_COST_COINS    = 8
LOBBY_SECONDS             = 120

# Latency thresholds (seconds)
LATENCY_WARNING_THRESHOLD = 60
LATENCY_SLOW_SINGLE       = 90
LATENCY_FAST_THRESHOLD    = 20

# ── EXAM QUESTION STYLES ──────────────────────────────────────────────────────
EXAM_QUESTION_STYLES = {
    "default": [
        "Application-level scenario questions",
        "Conceptual traps with plausible wrong answers",
        "Case-study based questions",
        "Data interpretation questions",
        "Exception/edge-case questions",
    ],
    "UPSC": [
        "Statement-based (Statement 1/2 correct?)",
        "Match the following columns",
        "Chronological ordering",
        "Assertion-Reason format",
        "Map-based and data interpretation",
    ],
    "JEE": [
        "Multi-concept integration problems",
        "Calculation-heavy numerical problems",
        "Graph/diagram interpretation",
        "Multiple correct options type",
        "Integer-type answer questions",
    ],
    "NEET": [
        "Diagram labeling questions",
        "Clinical scenario application",
        "Assertion-Reason format",
        "Exception questions (which is NOT correct)",
        "Compare and contrast structure/function",
    ],
    "GATE": [
        "NAT (Numerical Answer Type)",
        "MCQ with negative marking traps",
        "Algorithm trace/output prediction",
        "Design and analysis questions",
        "Previous year pattern questions",
    ],
    "CAT": [
        "Inference-based questions",
        "Data sufficiency",
        "Logical reasoning chains",
        "Reading comprehension traps",
        "Quantitative approximation",
    ],
}

ACHIEVEMENTS = [
    {"id": "first_blood",         "name": "First Blood",          "icon": "🎯", "desc": "Complete your first question",                   "xp": 50},
    {"id": "hot_streak",          "name": "Hot Streak",           "icon": "🔥", "desc": "Answer 5 correct in a row",                      "xp": 100},
    {"id": "century",             "name": "Century",              "icon": "💯", "desc": "Complete 100 questions",                         "xp": 200},
    {"id": "accuracy_ace",        "name": "Accuracy Ace",         "icon": "🏹", "desc": "Achieve 90%+ accuracy in a test",                "xp": 150},
    {"id": "week_warrior",        "name": "Week Warrior",         "icon": "⚔️",  "desc": "7-day study streak",                            "xp": 300},
    {"id": "chapter_master",      "name": "Chapter Master",       "icon": "📚", "desc": "Complete all sub-chapters in a chapter",         "xp": 250},
    {"id": "comeback_king",       "name": "Comeback King",        "icon": "👑", "desc": "Improve score 20%+ from previous test",          "xp": 200},
    {"id": "weak_slayer",         "name": "Weak Slayer",          "icon": "⚡", "desc": "Score 80%+ in weak training session",            "xp": 175},
    {"id": "night_owl",           "name": "Night Owl",            "icon": "🦉", "desc": "Study after 10 PM",                             "xp": 75},
    {"id": "early_bird",          "name": "Early Bird",           "icon": "🌅", "desc": "Study before 7 AM",                             "xp": 75},
    {"id": "perfectionist",       "name": "Perfectionist",        "icon": "💎", "desc": "Score 100% in any test",                        "xp": 500},
    {"id": "grind_mode",          "name": "Grind Mode",           "icon": "🦾", "desc": "Complete 10 tests total",                       "xp": 150},
    {"id": "chapter_unlock",      "name": "Unlocked",             "icon": "🔓", "desc": "Unlock your second chapter",                    "xp": 100},
    {"id": "speed_demon",         "name": "Speed Demon",          "icon": "💨", "desc": "Answer 10 questions under 5 min avg",           "xp": 125},
    {"id": "persistent",          "name": "Persistent",           "icon": "🧱", "desc": "Complete 5 weak training sessions",             "xp": 200},
    {"id": "dynamic_debut",       "name": "Dynamic Debut",        "icon": "🌀", "desc": "Complete your first dynamic session",           "xp": 100},
    {"id": "dynamic_marathon",    "name": "Marathon Mind",        "icon": "🏃", "desc": "Answer 50 questions in one dynamic session",    "xp": 300},
    {"id": "dynamic_ace",         "name": "Dynamic Ace",          "icon": "🃏", "desc": "80%+ accuracy in a dynamic session",            "xp": 200},
    {"id": "chapter_completed",   "name": "Chapter Champion",     "icon": "🏆", "desc": "Fully complete a chapter",                     "xp": 400},
    {"id": "coach_scholar",       "name": "Coach Scholar",        "icon": "🎓", "desc": "Have 10 coaching conversations",               "xp": 120},
    {"id": "weak_master",         "name": "Weak Area Conqueror",  "icon": "🛡️", "desc": "Turn 3 weak questions into correct answers",   "xp": 250},
    {"id": "teach_me_mode",       "name": "Teach Me Mode",        "icon": "📖", "desc": "Use AI coaching to learn from 5 wrong answers", "xp": 180},
    {"id": "real_exam_ready",     "name": "Exam Ready",           "icon": "🎖️", "desc": "Score 80%+ in dynamic real-exam mode",         "xp": 350},
    {"id": "thousand_questions",  "name": "Question Crusher",     "icon": "💪", "desc": "Answer 1000 total questions",                  "xp": 600},
    {"id": "mood_tracker",        "name": "Self-Aware Scholar",   "icon": "🧠", "desc": "Log mood for 7 days",                         "xp": 100},
    {"id": "three_chapters",      "name": "Triple Threat",        "icon": "🔱", "desc": "Complete 3 chapters",                         "xp": 500},
    {"id": "coin_collector",      "name": "Coin Collector",       "icon": "🪙", "desc": "Earn 100 coins",                              "xp": 80},
    {"id": "bookworm",            "name": "Bookworm",             "icon": "📌", "desc": "Bookmark 10 questions",                       "xp": 60},
    {"id": "challenge_champion",  "name": "Challenge Champion",   "icon": "🌟", "desc": "Complete 7 daily challenges",                 "xp": 280},
    {"id": "perfect_streak_5",    "name": "On Fire",              "icon": "🔥", "desc": "5-question correct streak",                   "xp": 100},
    {"id": "perfect_streak_10",   "name": "Unstoppable",          "icon": "⚡", "desc": "10-question correct streak",                  "xp": 250},
    {"id": "speed_solver",        "name": "Speed Solver",         "icon": "⚡", "desc": "Average under 20s per question in a session",  "xp": 200},
    {"id": "time_improver",       "name": "Time Optimizer",       "icon": "⏱️", "desc": "Improve avg decision time by 20%",            "xp": 150},
    {"id": "global_debut",        "name": "Global Debut",         "icon": "🌍", "desc": "Participate in your first global test",       "xp": 200},
    {"id": "global_podium",       "name": "Podium Finish",        "icon": "🥉", "desc": "Finish in top 3 in a global test",           "xp": 500},
    {"id": "global_champion",     "name": "Global Champion",      "icon": "🥇", "desc": "Win a global test",                          "xp": 1000},
]

XP_LEVELS = [0, 100, 250, 500, 900, 1400, 2100, 3000, 4200, 5700, 7500,
             10000, 13000, 17000, 22000]

# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="ExamAI API v4")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ── DATABASE ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _columns(conn, table: str) -> set:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}
    except Exception:
        return set()

SAFE_TO_DROP = {"questions", "test_attempts", "daily_tests", "weak_sessions", "sub_chapters"}

ADDABLE_COLS = [
    ("users",              "exam_type",             "TEXT"),
    ("users",              "exam_goal",             "TEXT"),
    ("users",              "chapters",              "TEXT"),
    ("users",              "setup_done",            "INTEGER DEFAULT 0"),
    ("users",              "receive_reminders",     "INTEGER DEFAULT 0"),
    ("users",              "xp",                   "INTEGER DEFAULT 0"),
    ("users",              "level",                "INTEGER DEFAULT 1"),
    ("users",              "achievements",         "TEXT DEFAULT '[]'"),
    ("users",              "mood_log",             "TEXT DEFAULT '[]'"),
    ("users",              "consecutive_correct",  "INTEGER DEFAULT 0"),
    ("users",              "coins",                "INTEGER DEFAULT 0"),
    ("users",              "avatar_color",         "TEXT DEFAULT '#00e5ff'"),
    ("users",              "bio",                  "TEXT DEFAULT ''"),
    ("users",              "daily_test_chapter_idx","INTEGER DEFAULT 0"),
    ("questions",          "sub_chapter",           "TEXT"),
    ("questions",          "difficulty",            "TEXT DEFAULT 'medium'"),
    ("questions",          "report_count",          "INTEGER DEFAULT 0"),
    ("test_attempts",      "time_taken",            "INTEGER DEFAULT 0"),
    ("test_attempts",      "session_type",          "TEXT DEFAULT 'daily'"),
    ("daily_tests",        "answers",               "TEXT"),
    ("daily_tests",        "score",                 "INTEGER DEFAULT 0"),
    ("daily_tests",        "total",                 "INTEGER DEFAULT 0"),
    ("daily_tests",        "completed",             "INTEGER DEFAULT 0"),
    ("daily_tests",        "completed_at",          "TEXT"),
    ("daily_tests",        "chapter_name",          "TEXT"),
    ("weak_sessions",      "answers",               "TEXT"),
    ("weak_sessions",      "score",                 "INTEGER DEFAULT 0"),
    ("weak_sessions",      "total",                 "INTEGER DEFAULT 0"),
    ("weak_sessions",      "completed",             "INTEGER DEFAULT 0"),
    ("dynamic_sessions",   "chapter",               "TEXT"),
    ("dynamic_sessions",   "exam_type",             "TEXT"),
    ("dynamic_sessions",   "question_pool",         "TEXT DEFAULT '[]'"),
    ("coach_messages",     "question_context",      "TEXT"),
]

REQUIRED_COLS = {
    "users":            {"id", "name", "email", "password_hash"},
    "sessions":         {"id", "user_id", "token"},
    "sub_chapters":     {"id", "user_id", "parent_chapter", "name", "order_index"},
    "questions":        {"id", "user_id", "chapter", "question", "options", "correct_answer", "explanation"},
    "test_attempts":    {"id", "user_id", "question_id", "user_answer", "is_correct"},
    "daily_tests":      {"id", "user_id", "test_date", "question_ids"},
    "weak_sessions":    {"id", "user_id", "question_ids"},
    "dynamic_sessions": {"id", "user_id", "seen_hashes", "score", "total", "is_active"},
    "dynamic_attempts": {"id", "session_id", "user_id", "question_text", "options",
                         "correct_answer", "explanation", "is_correct"},
}


def init_db():
    conn = get_db()

    for table, required in REQUIRED_COLS.items():
        if table not in SAFE_TO_DROP:
            continue
        existing = _columns(conn, table)
        if existing and not required.issubset(existing):
            missing = required - existing
            logger.warning("Table '%s' missing columns %s — dropping for rebuild.", table, missing)
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            name                    TEXT NOT NULL,
            email                   TEXT UNIQUE NOT NULL,
            password_hash           TEXT NOT NULL,
            exam_type               TEXT,
            exam_goal               TEXT,
            chapters                TEXT,
            setup_done              INTEGER DEFAULT 0,
            receive_reminders       INTEGER DEFAULT 0,
            xp                      INTEGER DEFAULT 0,
            level                   INTEGER DEFAULT 1,
            achievements            TEXT DEFAULT '[]',
            mood_log                TEXT DEFAULT '[]',
            consecutive_correct     INTEGER DEFAULT 0,
            coins                   INTEGER DEFAULT 0,
            avatar_color            TEXT DEFAULT '#00e5ff',
            bio                     TEXT DEFAULT '',
            daily_test_chapter_idx  INTEGER DEFAULT 0,
            created_at              TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            token      TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sub_chapters (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            parent_chapter TEXT NOT NULL,
            name           TEXT NOT NULL,
            order_index    INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS questions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            chapter        TEXT NOT NULL,
            sub_chapter    TEXT,
            question       TEXT NOT NULL,
            options        TEXT NOT NULL,
            correct_answer INTEGER NOT NULL,
            explanation    TEXT NOT NULL,
            difficulty     TEXT DEFAULT 'medium',
            report_count   INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS test_attempts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            user_answer  INTEGER,
            is_correct   INTEGER NOT NULL DEFAULT 0,
            time_taken   INTEGER DEFAULT 0,
            session_type TEXT DEFAULT 'daily',
            attempted_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS daily_tests (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            test_date    TEXT NOT NULL,
            question_ids TEXT NOT NULL,
            answers      TEXT,
            score        INTEGER DEFAULT 0,
            total        INTEGER DEFAULT 0,
            completed    INTEGER DEFAULT 0,
            completed_at TEXT,
            chapter_name TEXT
        );
        CREATE TABLE IF NOT EXISTS weak_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            question_ids TEXT NOT NULL,
            answers      TEXT,
            score        INTEGER DEFAULT 0,
            total        INTEGER DEFAULT 0,
            completed    INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mood_checkins (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            mood         INTEGER NOT NULL,
            energy       INTEGER NOT NULL,
            note         TEXT,
            checkin_date TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS coach_messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            message          TEXT NOT NULL,
            msg_type         TEXT DEFAULT 'coach',
            question_context TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS coach_memory (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL UNIQUE,
            summary      TEXT NOT NULL,
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dynamic_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            chapter       TEXT,
            exam_type     TEXT,
            seen_hashes   TEXT DEFAULT '[]',
            question_pool TEXT DEFAULT '[]',
            score         INTEGER DEFAULT 0,
            total         INTEGER DEFAULT 0,
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            ended_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS dynamic_attempts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id     INTEGER NOT NULL,
            user_id        INTEGER NOT NULL,
            question_text  TEXT NOT NULL,
            options        TEXT NOT NULL,
            correct_answer INTEGER NOT NULL,
            explanation    TEXT NOT NULL,
            user_answer    INTEGER,
            is_correct     INTEGER DEFAULT 0,
            time_taken     INTEGER DEFAULT 0,
            source         TEXT DEFAULT 'ai',
            attempted_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chapter_lessons (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            chapter      TEXT NOT NULL,
            content      TEXT NOT NULL,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, chapter)
        );
        CREATE TABLE IF NOT EXISTS chapter_completions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            chapter        TEXT NOT NULL,
            completed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, chapter)
        );
        CREATE TABLE IF NOT EXISTS teach_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            question_id     INTEGER,
            question_text   TEXT NOT NULL,
            correct_answer  INTEGER NOT NULL,
            user_answer     INTEGER,
            options         TEXT NOT NULL,
            explanation     TEXT NOT NULL,
            chapter         TEXT,
            completed       INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_plans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL UNIQUE,
            plan         TEXT NOT NULL,
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS bookmarks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            note         TEXT DEFAULT '',
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, question_id)
        );
        CREATE TABLE IF NOT EXISTS daily_challenges (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            challenge_date TEXT NOT NULL,
            question_text  TEXT NOT NULL,
            options        TEXT NOT NULL,
            correct_answer INTEGER NOT NULL,
            explanation    TEXT NOT NULL,
            chapter        TEXT,
            user_answer    INTEGER,
            completed      INTEGER DEFAULT 0,
            xp_reward      INTEGER DEFAULT 75,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_date)
        );
        CREATE TABLE IF NOT EXISTS question_reports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            question_id  INTEGER NOT NULL,
            reason       TEXT NOT NULL,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS powerup_uses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            powerup_type TEXT NOT NULL,
            question_id  INTEGER,
            result       TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Decision Latency Tracker ────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS question_latency (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            question_id    INTEGER,
            session_type   TEXT NOT NULL DEFAULT 'daily',
            time_taken_sec INTEGER NOT NULL DEFAULT 0,
            is_correct     INTEGER DEFAULT 0,
            chapter        TEXT,
            difficulty     TEXT DEFAULT 'medium',
            recorded_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS latency_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            session_type    TEXT NOT NULL,
            session_ref_id  INTEGER,
            total_questions INTEGER DEFAULT 0,
            avg_time_sec    REAL DEFAULT 0,
            slowest_sec     INTEGER DEFAULT 0,
            fastest_sec     INTEGER DEFAULT 0,
            slow_count      INTEGER DEFAULT 0,
            fast_count      INTEGER DEFAULT 0,
            chapter         TEXT,
            recorded_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Global test tables ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS global_tests (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT NOT NULL,
            exam_type        TEXT NOT NULL,
            topic            TEXT NOT NULL,
            question_ids     TEXT NOT NULL DEFAULT '[]',
            scheduled_at     TEXT NOT NULL,
            starts_at        TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 60,
            status           TEXT NOT NULL DEFAULT 'draft',
            created_by       TEXT NOT NULL DEFAULT 'admin',
            created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at         TEXT,
            winner_user_id   INTEGER,
            winner_name      TEXT,
            winner_score     INTEGER
        );
        CREATE TABLE IF NOT EXISTS global_questions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            global_test_id INTEGER NOT NULL,
            question       TEXT NOT NULL,
            options        TEXT NOT NULL,
            correct_answer INTEGER NOT NULL,
            explanation    TEXT NOT NULL,
            difficulty     TEXT DEFAULT 'medium',
            topic_tag      TEXT,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS global_participants (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            global_test_id INTEGER NOT NULL,
            user_id        INTEGER NOT NULL,
            joined_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at     TEXT,
            submitted_at   TEXT,
            answers        TEXT DEFAULT '{}',
            score          INTEGER DEFAULT 0,
            total          INTEGER DEFAULT 0,
            time_taken_sec INTEGER DEFAULT 0,
            rank           INTEGER,
            UNIQUE(global_test_id, user_id)
        );
    """)
    conn.commit()

    for table, column, col_def in ADDABLE_COLS:
        if column not in _columns(conn, table):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                conn.commit()
                logger.info("Migration: added %s.%s", table, column)
            except Exception as exc:
                logger.debug("Migration skip %s.%s — %s", table, column, exc)

    conn.close()


init_db()

# ── WEB SEARCH ────────────────────────────────────────────────────────────────

def search_topic_context(topic: str, exam_type: str = "", deep: bool = False) -> str:
    if not HAS_REQUESTS:
        return ""
    limit = 6000 if deep else 3500
    try:
        wiki_resp = _req_lib.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "titles": topic, "prop": "extracts",
                    "exintro": False, "explaintext": True, "format": "json",
                    "redirects": 1, "exchars": limit},
            timeout=8,
            headers={"User-Agent": "ExamAI/4.0"},
        )
        if wiki_resp.status_code == 200:
            data  = wiki_resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                text = page.get("extract", "")
                if text and len(text) > 300 and "-1" not in str(page.get("pageid", "")):
                    return text[:limit]
    except Exception as e:
        logger.debug("Wikipedia search failed: %s", e)
    return ""

# ── CORE HELPERS ──────────────────────────────────────────────────────────────

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def q_hash(text: str) -> str:
    return _hs.md5(text.strip().lower().encode()).hexdigest()[:16]

def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(401, "Not authenticated — missing Authorization header")
    authorization = authorization.strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated — expected 'Bearer <token>'")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Not authenticated — empty token")
    conn  = get_db()
    try:
        row = conn.execute(
            "SELECT s.*, u.id as uid, u.name, u.email, u.exam_type, u.exam_goal, "
            "u.chapters, u.setup_done, u.receive_reminders, u.xp, u.level, "
            "u.achievements, u.mood_log, u.consecutive_correct, u.coins, "
            "u.avatar_color, u.bio, u.daily_test_chapter_idx "
            "FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=?", (token,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(401, "Invalid or expired token — please log in again")
    return dict(row)

def parse_json(text: str):
    text = text.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").lstrip("JSON").strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    try:
        return json.loads(text)
    except Exception:
        pass
    for s, e in [('[', ']'), ('{', '}')]:
        i = text.find(s)
        j = text.rfind(e) + 1
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j])
            except Exception:
                pass
    raise ValueError("Cannot parse JSON from AI response")

def fmt_q(q):
    if q is None:
        return None
    keys = q.keys() if hasattr(q, "keys") else q
    return {
        "id": q["id"], "chapter": q["chapter"],
        "sub_chapter": q["sub_chapter"] if "sub_chapter" in keys else None,
        "question": q["question"],
        "options": json.loads(q["options"]) if isinstance(q["options"], str) else q["options"],
        "correct_answer": q["correct_answer"], "explanation": q["explanation"],
        "difficulty": q["difficulty"] if "difficulty" in keys else "medium",
    }

def safe_answer_int(ua):
    if ua is None:
        return None
    try:
        return int(ua)
    except (TypeError, ValueError):
        return None

def get_exam_style_hints(exam_type: str) -> str:
    for key in EXAM_QUESTION_STYLES:
        if key.upper() in (exam_type or "").upper():
            styles = EXAM_QUESTION_STYLES[key]
            return "\n".join(f"  - {s}" for s in styles)
    return "\n".join(f"  - {s}" for s in EXAM_QUESTION_STYLES["default"])

def normalize_correct_answer(ca, options: list) -> Optional[int]:
    if isinstance(ca, int) and 0 <= ca <= 3:
        return ca
    if isinstance(ca, str):
        letter_map = {"A": 0, "B": 1, "C": 2, "D": 3,
                      "a": 0, "b": 1, "c": 2, "d": 3}
        clean = ca.strip().rstrip(".")
        if clean in letter_map:
            return letter_map[clean]
        if clean in {"0", "1", "2", "3"}:
            return int(clean)
        for i, opt in enumerate(options or []):
            if str(opt).strip().lower() == clean.lower():
                return i
        if len(clean) >= 2 and clean[0].upper() in letter_map:
            return letter_map[clean[0].upper()]
    return None

def validate_question_dict(q: dict) -> Optional[dict]:
    if not isinstance(q, dict):
        return None
    question_text = str(q.get("question", "")).strip()
    if not question_text or len(question_text) < 15:
        return None
    options = q.get("options", [])
    if not isinstance(options, list):
        return None
    options = [str(o).strip() for o in options if str(o).strip()]
    if len(options) != 4:
        return None
    ca_raw = q.get("correct_answer")
    ca = normalize_correct_answer(ca_raw, options)
    if ca is None:
        return None
    if len(set(o.lower() for o in options)) < 4:
        return None
    explanation = str(q.get("explanation", "")).strip()
    if not explanation or len(explanation) < 10:
        explanation = f"The correct answer is option {ca + 1}: {options[ca]}."
    difficulty = str(q.get("difficulty", "medium")).lower()
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"
    return {
        "question": question_text,
        "options": options,
        "correct_answer": ca,
        "explanation": explanation,
        "difficulty": difficulty,
    }

def build_question_prompt(topic: str, exam_type: str, count: int,
                           style_hints: str, ctx_block: str = "",
                           seen_count: int = 0) -> str:
    diversity_note = (
        f"\nIMPORTANT: You have already generated {seen_count} questions on this topic. "
        "Make these questions on DIFFERENT sub-aspects — no repeats."
    ) if seen_count > 0 else ""

    return f"""You are an expert question setter for {exam_type} exam.{ctx_block}{diversity_note}

Generate exactly {count} multiple-choice questions about: "{topic}"

Question styles to use (mix these):
{style_hints}

OUTPUT FORMAT — You MUST follow this EXACTLY:
{{
  "questions": [
    {{
      "question": "Write the full question here",
      "options": [
        "First option text",
        "Second option text",
        "Third option text",
        "Fourth option text"
      ],
      "correct_answer": 2,
      "explanation": "Why option 3 (index 2) is correct. Why others are wrong.",
      "difficulty": "medium"
    }}
  ]
}}

STRICT RULES for correct_answer:
- It MUST be an INTEGER: 0, 1, 2, or 3
- 0 = first option is correct, 1 = second, 2 = third, 3 = fourth
- Double-check: options[correct_answer] must actually BE the right answer
- VARY the correct index — do not always use 0 or 1

STRICT RULES for options:
- All 4 options must be plausible (no obviously wrong distractors)
- Options must be distinct — no duplicates
- Do not include "All of the above" or "None of the above"

difficulty: must be exactly "easy", "medium", or "hard"

Return ONLY the JSON. No extra text before or after."""

# ── CHAPTER / PROGRESS HELPERS ────────────────────────────────────────────────

def chapter_stats(conn, user_id, chapter_name):
    s = conn.execute("""
        SELECT
            COUNT(DISTINCT q.id)                                          AS total,
            COUNT(DISTINCT CASE WHEN ta.id IS NOT NULL THEN q.id END)    AS attempted,
            COUNT(DISTINCT CASE WHEN ta.is_correct=1  THEN q.id END)     AS correct,
            SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END)             AS correct_attempts,
            COUNT(ta.id)                                                  AS total_attempts
        FROM questions q
        LEFT JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
        WHERE q.user_id=? AND q.chapter=?
    """, (user_id, user_id, chapter_name)).fetchone()

    if s is None:
        return 0, 0, 0
    attempted        = s["attempted"]        or 0
    correct_attempts = s["correct_attempts"] or 0
    total_attempts   = s["total_attempts"]   or 0
    acc = round(correct_attempts / max(total_attempts, 1) * 100, 1) if total_attempts > 0 else 0
    correct = s["correct"] or 0
    return attempted, correct, acc

def get_sub_chapter_progress(conn, user_id, chapter_name):
    subs = conn.execute(
        "SELECT name FROM sub_chapters WHERE user_id=? AND parent_chapter=? ORDER BY order_index",
        (user_id, chapter_name)).fetchall()
    if not subs:
        return 0, 0
    practiced = 0
    for sub in subs:
        cnt = conn.execute("""
            SELECT COUNT(ta.id) as cnt FROM questions q
            JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
            WHERE q.user_id=? AND q.sub_chapter=?
        """, (user_id, user_id, sub["name"])).fetchone()["cnt"] or 0
        if cnt >= REQUIRED_SUB_ATTEMPTS:
            practiced += 1
    return practiced, len(subs)

def is_unlocked(conn, user_id, chapters, idx):
    if idx == 0:
        return True
    prev = chapters[idx - 1]
    att, cor, acc = chapter_stats(conn, user_id, prev)
    subs_practiced, subs_total = get_sub_chapter_progress(conn, user_id, prev)
    if subs_total == 0:
        return att >= UNLOCK_REQUIRED_ATTEMPTS and acc >= UNLOCK_REQUIRED_ACCURACY
    return (subs_practiced >= subs_total
            and att >= UNLOCK_REQUIRED_ATTEMPTS
            and acc >= UNLOCK_REQUIRED_ACCURACY)

def is_chapter_complete(conn, user_id, chapters, idx) -> bool:
    ch = chapters[idx]
    att, cor, acc = chapter_stats(conn, user_id, ch)
    subs_p, subs_t = get_sub_chapter_progress(conn, user_id, ch)
    if subs_t == 0:
        return False
    return (subs_p >= subs_t
            and att >= UNLOCK_REQUIRED_ATTEMPTS
            and acc >= UNLOCK_REQUIRED_ACCURACY)

def get_active_daily_chapter(conn, user_id: int, chapters: list) -> tuple:
    if not chapters:
        return None, 0
    for i, ch in enumerate(chapters):
        if not is_unlocked(conn, user_id, chapters, i):
            idx = max(0, i - 1)
            return chapters[idx], idx
        if not is_chapter_complete(conn, user_id, chapters, i):
            return ch, i
    last = len(chapters) - 1
    return chapters[last], last

# ── LATENCY HELPERS ───────────────────────────────────────────────────────────

def record_latency_batch(conn, user_id: int, answers: dict, time_taken: dict,
                          session_type: str, q_lookup_fn):
    records = []
    for qid_str, ua in answers.items():
        try:
            qid = int(qid_str)
        except (ValueError, TypeError):
            continue
        secs = int(time_taken.get(str(qid), 0) or 0)
        if secs <= 0:
            continue
        chapter, difficulty = q_lookup_fn(qid)
        ua_int = safe_answer_int(ua)
        records.append((user_id, qid, session_type, secs, 0, chapter, difficulty))

    for r in records:
        conn.execute(
            "INSERT INTO question_latency (user_id, question_id, session_type, time_taken_sec, "
            "is_correct, chapter, difficulty) VALUES (?,?,?,?,?,?,?)", r)

    if records:
        conn.commit()
    return records

def compute_latency_stats(conn, user_id: int, limit_days: int = 30) -> dict:
    cutoff = (datetime.now() - timedelta(days=limit_days)).isoformat()
    rows = conn.execute(
        "SELECT time_taken_sec, is_correct, difficulty, chapter FROM question_latency "
        "WHERE user_id=? AND recorded_at >= ? AND time_taken_sec > 0",
        (user_id, cutoff)).fetchall()
    if not rows:
        return {"avg_time": 0, "median_time": 0, "slow_count": 0, "fast_count": 0,
                "total_tracked": 0, "slowest_chapter": None, "trend": "no_data"}

    times = sorted([r["time_taken_sec"] for r in rows])
    n = len(times)
    avg = sum(times) / n
    median = times[n // 2]
    slow = sum(1 for t in times if t > LATENCY_SLOW_SINGLE)
    fast = sum(1 for t in times if t < LATENCY_FAST_THRESHOLD)

    ch_times: dict = {}
    for r in rows:
        ch = r["chapter"] or "Unknown"
        if ch not in ch_times:
            ch_times[ch] = []
        ch_times[ch].append(r["time_taken_sec"])
    ch_avgs = {ch: sum(ts) / len(ts) for ch, ts in ch_times.items()}
    slowest_ch = max(ch_avgs, key=lambda x: ch_avgs[x]) if ch_avgs else None

    recent_cut = (datetime.now() - timedelta(days=7)).isoformat()
    prev_cut   = (datetime.now() - timedelta(days=14)).isoformat()
    recent_rows = conn.execute(
        "SELECT time_taken_sec FROM question_latency WHERE user_id=? AND recorded_at>=? AND time_taken_sec>0",
        (user_id, recent_cut)).fetchall()
    prev_rows = conn.execute(
        "SELECT time_taken_sec FROM question_latency WHERE user_id=? AND recorded_at>=? AND recorded_at<? AND time_taken_sec>0",
        (user_id, prev_cut, recent_cut)).fetchall()
    recent_avg = sum(r["time_taken_sec"] for r in recent_rows) / max(len(recent_rows), 1)
    prev_avg   = sum(r["time_taken_sec"] for r in prev_rows)   / max(len(prev_rows), 1)
    if not prev_rows:
        trend = "no_data"
    elif recent_avg < prev_avg * 0.85:
        trend = "improving"
    elif recent_avg > prev_avg * 1.15:
        trend = "slowing"
    else:
        trend = "stable"

    return {
        "avg_time": round(avg, 1),
        "median_time": median,
        "slow_count": slow,
        "fast_count": fast,
        "total_tracked": n,
        "slowest_chapter": slowest_ch,
        "slowest_chapter_avg": round(ch_avgs.get(slowest_ch, 0), 1) if slowest_ch else 0,
        "chapter_breakdown": {ch: round(v, 1) for ch, v in ch_avgs.items()},
        "trend": trend,
        "recent_avg": round(recent_avg, 1),
        "prev_avg": round(prev_avg, 1),
        "times_percentile_90": times[int(n * 0.9)] if n >= 10 else max(times),
    }

def get_latency_alerts(stats: dict) -> list:
    alerts = []
    if stats["avg_time"] > LATENCY_WARNING_THRESHOLD:
        alerts.append({
            "type": "slow_average",
            "severity": "high",
            "message": f"Your average decision time is {stats['avg_time']}s — above the {LATENCY_WARNING_THRESHOLD}s target.",
            "tip": "Try reading options before the full question to eliminate obvious wrong answers faster."
        })
    if stats["slow_count"] > stats["total_tracked"] * 0.3 and stats["total_tracked"] >= 10:
        alerts.append({
            "type": "too_many_slow",
            "severity": "medium",
            "message": f"{stats['slow_count']} questions took over {LATENCY_SLOW_SINGLE}s — that's {round(stats['slow_count']/max(stats['total_tracked'],1)*100)}% of your attempts.",
            "tip": "If you're stuck after 30s, mark it and move on. Return if time allows."
        })
    if stats["trend"] == "slowing":
        alerts.append({
            "type": "slowing_trend",
            "severity": "medium",
            "message": f"Your decision speed has slowed — recent avg {stats['recent_avg']}s vs {stats['prev_avg']}s previously.",
            "tip": "Review your weakest chapter under a timer — simulate real exam pressure."
        })
    if stats["slowest_chapter"] and stats["slowest_chapter_avg"] > LATENCY_WARNING_THRESHOLD:
        alerts.append({
            "type": "slow_chapter",
            "severity": "medium",
            "message": f"Slowest chapter: '{stats['slowest_chapter']}' averaging {stats['slowest_chapter_avg']}s.",
            "tip": f"Spend 15 min doing a quick flashcard review of '{stats['slowest_chapter']}' core concepts."
        })
    if stats["trend"] == "improving":
        alerts.append({
            "type": "improving",
            "severity": "info",
            "message": f"Your speed is improving! Recent avg {stats['recent_avg']}s vs {stats['prev_avg']}s previously. 🚀",
            "tip": "Keep the momentum — try dynamic mode to push further."
        })
    return alerts

# ── GROQ WRAPPERS ─────────────────────────────────────────────────────────────

def groq_chat(prompt: str, temperature=0.6, json_mode=True) -> str:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 4096,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content

def groq_chat_with_history(messages: list, temperature=0.7) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=800,
    )
    return resp.choices[0].message.content

# ── GAMIFICATION ─────────────────────────────────────────────────────────────

def get_level(xp):
    for i, threshold in enumerate(XP_LEVELS):
        if xp < threshold:
            return max(1, i)
    return len(XP_LEVELS)

def award_xp(conn, user_id, amount, reason=""):
    conn.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (amount, user_id))
    conn.commit()
    row = conn.execute("SELECT xp FROM users WHERE id=?", (user_id,)).fetchone()
    new_xp = row["xp"] if row else 0
    new_level = get_level(new_xp)
    conn.execute("UPDATE users SET level=? WHERE id=?", (new_level, user_id))
    conn.commit()
    return new_xp, new_level

def award_coins(conn, user_id, amount):
    conn.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (amount, user_id))
    conn.commit()
    row = conn.execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
    return row["coins"] if row else 0

def spend_coins(conn, user_id, amount) -> bool:
    row = conn.execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or (row["coins"] or 0) < amount:
        return False
    conn.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (amount, user_id))
    conn.commit()
    return True

def update_consecutive_correct(conn, user_id, is_correct: bool) -> int:
    if is_correct:
        conn.execute("UPDATE users SET consecutive_correct = consecutive_correct + 1 WHERE id=?", (user_id,))
    else:
        conn.execute("UPDATE users SET consecutive_correct = 0 WHERE id=?", (user_id,))
    conn.commit()
    row = conn.execute("SELECT consecutive_correct FROM users WHERE id=?", (user_id,)).fetchone()
    return row["consecutive_correct"] if row else 0

def get_streak(conn, user_id: int) -> int:
    streak = 0
    hist = conn.execute(
        "SELECT test_date FROM daily_tests WHERE user_id=? AND completed=1 ORDER BY test_date DESC",
        (user_id,)).fetchall()
    if hist:
        check = date.today()
        for r in hist:
            try:
                d = date.fromisoformat(r["test_date"])
            except Exception:
                break
            if d == check or d == check - timedelta(1):
                streak += 1
                check = d - timedelta(1)
            else:
                break
    return streak

def check_and_award_achievements(conn, user_id, context: dict) -> list:
    row = conn.execute("SELECT achievements FROM users WHERE id=?", (user_id,)).fetchone()
    earned = json.loads(row["achievements"] or "[]") if row else []
    earned_ids = {a["id"] for a in earned}
    new_achievements = []

    total_att    = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (user_id,)).fetchone()[0]
    tests_done   = conn.execute("SELECT COUNT(*) FROM daily_tests WHERE user_id=? AND completed=1", (user_id,)).fetchone()[0]
    weak_done    = conn.execute("SELECT COUNT(*) FROM weak_sessions WHERE user_id=? AND completed=1", (user_id,)).fetchone()[0]
    dyn_done     = conn.execute("SELECT COUNT(*) FROM dynamic_sessions WHERE user_id=? AND is_active=0", (user_id,)).fetchone()[0]
    coach_count  = conn.execute("SELECT COUNT(*) FROM coach_messages WHERE user_id=? AND msg_type='user'", (user_id,)).fetchone()[0]
    mood_days    = conn.execute("SELECT COUNT(DISTINCT date(checkin_date)) FROM mood_checkins WHERE user_id=?", (user_id,)).fetchone()[0]
    teach_done   = conn.execute("SELECT COUNT(*) FROM teach_sessions WHERE user_id=? AND completed=1", (user_id,)).fetchone()[0]
    chapters_done= conn.execute("SELECT COUNT(*) FROM chapter_completions WHERE user_id=?", (user_id,)).fetchone()[0]
    bookmark_cnt = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=?", (user_id,)).fetchone()[0]
    challenge_cnt= conn.execute("SELECT COUNT(*) FROM daily_challenges WHERE user_id=? AND completed=1", (user_id,)).fetchone()[0]
    coins_row    = conn.execute("SELECT COALESCE(coins, 0) as coins FROM users WHERE id=?", (user_id,)).fetchone()
    total_coins_ever = (coins_row["coins"] if coins_row else 0)

    global_parts = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND submitted_at IS NOT NULL",
        (user_id,)).fetchone()[0]
    global_wins = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND rank=1",
        (user_id,)).fetchone()[0]
    global_podium = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND rank<=3",
        (user_id,)).fetchone()[0]

    user_chapters_row = conn.execute("SELECT chapters FROM users WHERE id=?", (user_id,)).fetchone()
    user_chapters = json.loads(user_chapters_row["chapters"] or "[]") if user_chapters_row and user_chapters_row["chapters"] else []
    has_chapter_master = False
    for ch in user_chapters:
        sp, st = get_sub_chapter_progress(conn, user_id, ch)
        if st > 0 and sp >= st:
            has_chapter_master = True
            break

    weak_turned = conn.execute("""
        SELECT COUNT(DISTINCT q.id) FROM questions q
        WHERE q.user_id=?
          AND EXISTS (SELECT 1 FROM test_attempts ta WHERE ta.question_id=q.id AND ta.user_id=? AND ta.is_correct=0)
          AND EXISTS (SELECT 1 FROM test_attempts ta2 WHERE ta2.question_id=q.id AND ta2.user_id=? AND ta2.is_correct=1
                      AND ta2.attempted_at > (SELECT MAX(ta3.attempted_at) FROM test_attempts ta3
                                              WHERE ta3.question_id=q.id AND ta3.user_id=? AND ta3.is_correct=0))
    """, (user_id, user_id, user_id, user_id)).fetchone()[0]

    streak = get_streak(conn, user_id)
    hour     = datetime.now().hour
    score    = context.get("score", 0)
    total    = context.get("total", 1)
    pct      = score / max(total, 1) * 100
    prev_pct = context.get("prev_pct", 0)
    mode     = context.get("mode", "")
    dyn_total= context.get("dyn_total", 0)
    consec   = conn.execute("SELECT consecutive_correct FROM users WHERE id=?", (user_id,)).fetchone()
    consec_n = consec["consecutive_correct"] if consec else 0

    lat_stats = compute_latency_stats(conn, user_id, limit_days=30)
    is_speed_solver = (lat_stats["avg_time"] < LATENCY_FAST_THRESHOLD and
                       lat_stats["total_tracked"] >= 10)
    is_time_improver = (lat_stats["trend"] == "improving" and
                        lat_stats["prev_avg"] > 0 and
                        (lat_stats["prev_avg"] - lat_stats["recent_avg"]) / lat_stats["prev_avg"] >= 0.2)

    checks = [
        ("first_blood",        total_att >= 1),
        ("century",            total_att >= 100),
        ("thousand_questions", total_att >= 1000),
        ("accuracy_ace",       pct >= 90 and total >= 5),
        ("perfectionist",      pct >= 100 and total >= 5),
        ("week_warrior",       streak >= 7),
        ("comeback_king",      pct - prev_pct >= 20 and prev_pct > 0),
        ("weak_slayer",        mode == "weak" and pct >= 80),
        ("night_owl",          hour >= 22),
        ("early_bird",         hour < 7),
        ("grind_mode",         tests_done >= 10),
        ("persistent",         weak_done >= 5),
        ("dynamic_debut",      dyn_done >= 1),
        ("dynamic_marathon",   mode == "dynamic" and dyn_total >= 50),
        ("dynamic_ace",        mode == "dynamic" and pct >= 80 and total >= 10),
        ("real_exam_ready",    mode == "dynamic" and pct >= 80 and total >= 20),
        ("chapter_completed",  context.get("chapter_completed", False)),
        ("chapter_master",     has_chapter_master),
        ("coach_scholar",      coach_count >= 10),
        ("weak_master",        weak_turned >= 3),
        ("teach_me_mode",      teach_done >= 5),
        ("mood_tracker",       mood_days >= 7),
        ("three_chapters",     chapters_done >= 3),
        ("hot_streak",         consec_n >= 5),
        ("coin_collector",     total_coins_ever >= 100),
        ("bookworm",           bookmark_cnt >= 10),
        ("challenge_champion", challenge_cnt >= 7),
        ("perfect_streak_5",   consec_n >= 5),
        ("perfect_streak_10",  consec_n >= 10),
        ("speed_solver",       is_speed_solver),
        ("time_improver",      is_time_improver),
        ("global_debut",       global_parts >= 1),
        ("global_podium",      global_podium >= 1),
        ("global_champion",    global_wins >= 1),
    ]

    for ach_id, condition in checks:
        if condition and ach_id not in earned_ids:
            ach_def = next((a for a in ACHIEVEMENTS if a["id"] == ach_id), None)
            if ach_def:
                ach_entry = {**ach_def, "earned_at": datetime.now().isoformat()}
                earned.append(ach_entry)
                earned_ids.add(ach_id)
                new_achievements.append(ach_entry)
                award_xp(conn, user_id, ach_def["xp"], f"achievement:{ach_id}")

    conn.execute("UPDATE users SET achievements=? WHERE id=?", (json.dumps(earned), user_id))
    conn.commit()
    return new_achievements

# ── EMAIL ─────────────────────────────────────────────────────────────────────

def send_email(to_email: str, subject: str, html_body: str):
    if not SMTP_USER or not SMTP_PASS:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"ExamAI <{SMTP_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error("Email error: %s", exc)
        return False

# ── SCHEDULER ─────────────────────────────────────────────────────────────────

def job_morning_reminders():
    conn  = get_db()
    users = conn.execute("SELECT name, email FROM users WHERE receive_reminders=1 AND setup_done=1").fetchall()
    conn.close()
    for u in users:
        html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;background:#050a14;color:#d0eeff;padding:2rem;border-radius:12px">
          <h2 style="color:#00e5ff">Good Morning, {u['name']}! 🌅</h2>
          <p>Your daily test is ready. Stay consistent — champions are built daily.</p>
          <a href="{APP_URL}" style="display:inline-block;background:linear-gradient(135deg,#00e5ff,#00b8d4);color:#000;font-weight:700;padding:.85rem 2rem;border-radius:100px;text-decoration:none">⚡ Start Today's Test</a>
        </div>"""
        send_email(u["email"], "Your Daily ExamAI Test is Ready!", html)

def job_evening_results():
    conn  = get_db()
    today = date.today().isoformat()
    users = conn.execute("SELECT id, name, email FROM users WHERE receive_reminders=1 AND setup_done=1").fetchall()
    for u in users:
        test = conn.execute(
            "SELECT score, total FROM daily_tests WHERE user_id=? AND test_date=? AND completed=1",
            (u["id"], today)).fetchone()
        sc  = test["score"] if test else 0
        tot = test["total"] if test else 0
        pct = round(sc / max(tot, 1) * 100, 1) if tot else 0
        color = "#00ff88" if pct >= 75 else "#ffd700" if pct >= 50 else "#ff2d6e"
        html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;background:#050a14;color:#d0eeff;padding:2rem;border-radius:12px">
          <h2 style="color:#00e5ff">Daily Results — {u['name']}</h2>
          <div style="text-align:center;padding:1.5rem;background:#0a2035;border-radius:10px;margin:1rem 0">
            <div style="font-size:3rem;font-weight:900;color:{color}">{pct}%</div>
            <div style="color:#6a9ab8">{sc} / {tot} correct</div>
          </div>
          <a href="{APP_URL}" style="display:inline-block;background:linear-gradient(135deg,#ffd700,#cc9900);color:#000;font-weight:700;padding:.75rem 1.75rem;border-radius:100px;text-decoration:none">View Analytics →</a>
        </div>"""
        send_email(u["email"], f"ExamAI Results — {pct}% today", html)
    conn.close()

def job_compress_coach_memory():
    conn  = get_db()
    users = conn.execute("SELECT id, name, exam_type FROM users WHERE setup_done=1").fetchall()
    for u in users:
        _compress_coach_memory(conn, u["id"], u["name"], u["exam_type"] or "exam")
    conn.close()

if HAS_SCHEDULER:
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(job_morning_reminders,    CronTrigger(hour=8,  minute=0, timezone=TZ))
    scheduler.add_job(job_evening_results,       CronTrigger(hour=20, minute=0, timezone=TZ))
    scheduler.add_job(job_compress_coach_memory, CronTrigger(hour=2,  minute=0, timezone=TZ))
    scheduler.start()

# ── PYDANTIC MODELS ───────────────────────────────────────────────────────────

class RegisterReq(BaseModel):
    name: str; email: str; password: str

class LoginReq(BaseModel):
    email: str; password: str

class UpdateProfileReq(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    avatar_color: Optional[str] = None
    receive_reminders: Optional[bool] = None

class ExamSetupReq(BaseModel):
    exam_type: str
    exam_goal: str = ""
    custom_chapters: Optional[List[str]] = None

class GenQuestionsReq(BaseModel):
    chapter: str; sub_chapter: Optional[str] = None; count: int = 10

class SubmitTestReq(BaseModel):
    test_id: int; answers: dict; time_taken: dict = {}; session_type: str = "daily"

class WeakSessionSubmitReq(BaseModel):
    session_id: int; answers: dict; time_taken: dict = {}

class ExplainReq(BaseModel):
    question_id: int; user_answer: int

class LearnReq(BaseModel):
    chapter: str; sub_chapter: str

class NotifReq(BaseModel):
    receive_reminders: bool

class ChapterPracticeReq(BaseModel):
    answers: dict; time_taken: dict = {}; session_type: str = "chapter"

class MoodCheckinReq(BaseModel):
    mood: int; energy: int; note: str = ""

class CoachChatReq(BaseModel):
    message: str
    context: dict = {}
    question_context: Optional[dict] = None

class TeachMeReq(BaseModel):
    question_id: Optional[int] = None
    question_text: Optional[str] = None
    options: Optional[list] = None
    correct_answer: Optional[int] = None
    user_answer: Optional[int] = None
    explanation: Optional[str] = None
    chapter: Optional[str] = None

class TeachMeReplyReq(BaseModel):
    session_id: int
    message: str

class DynamicStartReq(BaseModel):
    chapter: Optional[str] = None

class DynamicAnswerReq(BaseModel):
    attempt_id: int
    user_answer: int
    time_taken: int = 0

class DynamicStopReq(BaseModel):
    session_id: int

class StudyPlanReq(BaseModel):
    exam_date: Optional[str] = None
    daily_hours: Optional[float] = 2.0
    focus_chapters: Optional[list] = None

class BookmarkReq(BaseModel):
    question_id: int
    note: str = ""

class ReportQuestionReq(BaseModel):
    question_id: int
    reason: str

class UseHintReq(BaseModel):
    question_id: int
    hint_type: str

class DailyChallengeSubmitReq(BaseModel):
    user_answer: int

class GlobalTestJoinReq(BaseModel):
    global_test_id: int

class GlobalTestSubmitReq(BaseModel):
    global_test_id: int
    answers: dict
    time_taken_sec: int = 0

class LatencyCoachReq(BaseModel):
    chapter: Optional[str] = None

# ── COACH TRIGGER HELPER ──────────────────────────────────────────────────────
# FIX: Build a coaching trigger payload for wrong answers so the frontend can
#      automatically redirect the user to AI coach after a wrong answer.

def build_coaching_trigger(question_data: dict, user_answer: int) -> dict:
    """
    Returns a coaching_trigger dict that the frontend uses to auto-open
    the AI Coach / Teach-Me flow after a wrong answer.
    """
    opts = question_data.get("options", [])
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except Exception:
            opts = []
    return {
        "should_coach": True,
        "question_id": question_data.get("id"),
        "question_text": question_data.get("question", ""),
        "options": opts,
        "correct_answer": question_data.get("correct_answer"),
        "user_answer": user_answer,
        "explanation": question_data.get("explanation", ""),
        "chapter": question_data.get("chapter", ""),
        "message": "You got this wrong — your AI coach can help you understand it now!",
    }

# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
def register(req: RegisterReq):
    if not req.name or not req.name.strip():
        raise HTTPException(400, "Name is required")
    if not req.email or "@" not in req.email:
        raise HTTPException(400, "Valid email is required")
    if not req.password or len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (name,email,password_hash) VALUES (?,?,?)",
                     (req.name.strip(), req.email.lower().strip(), hash_password(req.password)))
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        token   = secrets.token_hex(32)
        conn.execute("INSERT INTO sessions (user_id,token) VALUES (?,?)", (user_id, token))
        conn.commit()
        return {"token": token, "user": {"id": user_id, "name": req.name.strip(),
                "email": req.email.lower().strip(), "setup_done": False, "xp": 0, "level": 1,
                "coins": 0, "avatar_color": "#00e5ff"}}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Email already registered")
    finally:
        conn.close()

@app.post("/api/auth/login")
def login(req: LoginReq):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                        (req.email.lower().strip(), hash_password(req.password))).fetchone()
    if not user:
        conn.close()
        raise HTTPException(401, "Invalid email or password")
    user  = dict(user)
    token = secrets.token_hex(32)
    conn.execute("INSERT INTO sessions (user_id,token) VALUES (?,?)", (user["id"], token))
    conn.commit(); conn.close()
    return {"token": token, "user": {
        "id": user["id"], "name": user["name"], "email": user["email"],
        "exam_type": user["exam_type"], "setup_done": bool(user["setup_done"]),
        "receive_reminders": bool(user["receive_reminders"]),
        "xp": user.get("xp", 0), "level": user.get("level", 1),
        "coins": user.get("coins", 0), "avatar_color": user.get("avatar_color", "#00e5ff"),
        "bio": user.get("bio", ""),
        "achievements": json.loads(user.get("achievements") or "[]"),
    }}

@app.post("/api/auth/logout")
def logout(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"success": True}
    token = authorization.split(" ", 1)[1].strip()
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Logged out"}

# ── USER PROFILE ──────────────────────────────────────────────────────────────

@app.get("/api/user/profile")
def profile(u=Depends(get_current_user)):
    conn = get_db()
    uid = u["uid"]
    streak = get_streak(conn, uid)
    total_att = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    total_cor = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=? AND is_correct=1", (uid,)).fetchone()[0]
    acc = round(total_cor / max(total_att, 1) * 100, 1) if total_att else 0
    best_day = conn.execute("""
        SELECT test_date, score, total,
               ROUND(CAST(score AS FLOAT)/MAX(total,1)*100, 1) as pct
        FROM daily_tests WHERE user_id=? AND completed=1
        ORDER BY pct DESC LIMIT 1
    """, (uid,)).fetchone()
    member_since = conn.execute("SELECT created_at FROM users WHERE id=?", (uid,)).fetchone()
    bookmarks = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=?", (uid,)).fetchone()[0]
    chapters_done = conn.execute("SELECT COUNT(*) FROM chapter_completions WHERE user_id=?", (uid,)).fetchone()[0]
    xp = u.get("xp", 0)
    level = u.get("level", 1)
    xp_for_next = XP_LEVELS[min(level, len(XP_LEVELS)-1)] if level < len(XP_LEVELS) else None
    xp_prev = XP_LEVELS[max(0, level-1)]
    xp_progress = round((xp - xp_prev) / max(1, (xp_for_next or xp+1) - xp_prev) * 100, 1) if xp_for_next else 100

    global_tests_done = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND submitted_at IS NOT NULL",
        (uid,)).fetchone()[0]
    best_global_rank = conn.execute(
        "SELECT MIN(rank) FROM global_participants WHERE user_id=? AND rank IS NOT NULL",
        (uid,)).fetchone()[0]

    challenge_streak = 0
    ch_hist = conn.execute(
        "SELECT challenge_date FROM daily_challenges WHERE user_id=? AND completed=1 ORDER BY challenge_date DESC",
        (uid,)).fetchall()
    if ch_hist:
        check = date.today()
        for r in ch_hist:
            try:
                d = date.fromisoformat(r["challenge_date"])
                if d == check or d == check - timedelta(1):
                    challenge_streak += 1
                    check = d - timedelta(1)
                else:
                    break
            except Exception:
                break

    lat_stats = compute_latency_stats(conn, uid, 30)
    conn.close()
    return {
        "id": uid, "name": u["name"], "email": u["email"],
        "exam_type": u["exam_type"], "exam_goal": u["exam_goal"],
        "setup_done": bool(u["setup_done"]),
        "receive_reminders": bool(u["receive_reminders"]),
        "chapters": json.loads(u["chapters"]) if u["chapters"] else [],
        "xp": xp, "level": level,
        "xp_progress": xp_progress, "xp_for_next": xp_for_next,
        "achievements": json.loads(u.get("achievements") or "[]"),
        "consecutive_correct": u.get("consecutive_correct", 0),
        "coins": u.get("coins", 0),
        "avatar_color": u.get("avatar_color", "#00e5ff"),
        "bio": u.get("bio", ""),
        "stats": {
            "total_attempted": total_att,
            "total_correct": total_cor,
            "overall_accuracy": acc,
            "study_streak": streak,
            "challenge_streak": challenge_streak,
            "bookmarks": bookmarks,
            "chapters_completed": chapters_done,
            "best_day": dict(best_day) if best_day else None,
            "global_tests_completed": global_tests_done,
            "best_global_rank": best_global_rank,
            "avg_decision_time_sec": lat_stats.get("avg_time", 0),
        },
        "member_since": member_since["created_at"] if member_since else None,
    }

@app.put("/api/user/profile")
def update_profile(req: UpdateProfileReq, u=Depends(get_current_user)):
    conn = get_db()
    updates, values = [], []
    if req.name is not None and req.name.strip():
        updates.append("name=?"); values.append(req.name.strip()[:80])
    if req.bio is not None:
        updates.append("bio=?"); values.append(req.bio[:200])
    if req.avatar_color is not None:
        color = req.avatar_color.strip()
        if color.startswith("#") and len(color) in (4, 7):
            updates.append("avatar_color=?"); values.append(color)
    if req.receive_reminders is not None:
        updates.append("receive_reminders=?"); values.append(1 if req.receive_reminders else 0)
    if updates:
        values.append(u["uid"])
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", values)
        conn.commit()
    conn.close()
    return {"success": True, "message": "Profile updated"}

@app.post("/api/user/notifications")
def set_notifications(req: NotifReq, u=Depends(get_current_user)):
    conn = get_db()
    conn.execute("UPDATE users SET receive_reminders=? WHERE id=?",
                 (1 if req.receive_reminders else 0, u["uid"]))
    conn.commit(); conn.close()
    return {"receive_reminders": req.receive_reminders}

# ── EXAM SETUP ────────────────────────────────────────────────────────────────

@app.post("/api/exam/setup")
def setup_exam(req: ExamSetupReq, u=Depends(get_current_user)):
    exam_type = (req.exam_type or "").strip()
    if not exam_type:
        raise HTTPException(400, "exam_type is required")

    if req.custom_chapters and isinstance(req.custom_chapters, list) and len(req.custom_chapters) >= 3:
        chapters = [str(c).strip() for c in req.custom_chapters if str(c).strip()][:12]
        if len(chapters) < 3:
            raise HTTPException(400, "Please provide at least 3 chapters")
    else:
        prompt = f"""You are an expert exam coach. The student is preparing for: {exam_type}
{("Additional info: " + req.exam_goal) if req.exam_goal else ""}

Generate exactly 12 ordered main chapters/topics for this exam syllabus.
Return JSON: {{"chapters": ["Topic 1", "Topic 2", ...]}}
Make them specific to {exam_type} syllabus, ordered from foundational to advanced.
If this is a custom or general subject, generate relevant foundational-to-advanced topics.
Return ONLY valid JSON."""

        try:
            raw = groq_chat(prompt, temperature=0.4)
            data = parse_json(raw)
            chapters = data.get("chapters", data) if isinstance(data, dict) else data
            if not isinstance(chapters, list):
                chapters = []
            chapters = [str(c).strip() for c in chapters if str(c).strip()][:12]
        except Exception as exc:
            logger.error("Chapter generation failed: %s", exc)
            chapters = []

        if len(chapters) < 3:
            logger.warning("AI returned insufficient chapters for '%s', using fallback", exam_type)
            chapters = [
                f"{exam_type} — Fundamentals",
                f"{exam_type} — Core Concepts",
                f"{exam_type} — Intermediate Topics",
                f"{exam_type} — Advanced Concepts",
                f"{exam_type} — Problem Solving",
                f"{exam_type} — Applications",
                f"{exam_type} — Case Studies",
                f"{exam_type} — Practice & Revision",
            ]

    conn = get_db()
    conn.execute(
        "UPDATE users SET exam_type=?,exam_goal=?,chapters=?,setup_done=1,daily_test_chapter_idx=0 WHERE id=?",
        (exam_type, req.exam_goal, json.dumps(chapters), u["uid"]))
    conn.commit()
    award_xp(conn, u["uid"], 50, "setup_complete")
    conn.close()
    return {"chapters": chapters, "exam_type": exam_type}

# ── CHAPTERS ──────────────────────────────────────────────────────────────────

@app.get("/api/exam/chapters")
def get_chapters(u=Depends(get_current_user)):
    conn = get_db()
    row  = conn.execute("SELECT chapters FROM users WHERE id=?", (u["uid"],)).fetchone()
    if not row or not row["chapters"]:
        conn.close()
        return {"chapters": []}
    chapters = json.loads(row["chapters"])
    result   = []
    for i, ch in enumerate(chapters):
        att, cor, acc    = chapter_stats(conn, u["uid"], ch)
        unlocked         = is_unlocked(conn, u["uid"], chapters, i)
        complete         = is_chapter_complete(conn, u["uid"], chapters, i)
        total_qs         = conn.execute(
            "SELECT COUNT(DISTINCT id) FROM questions WHERE user_id=? AND chapter=?",
            (u["uid"], ch)).fetchone()[0]
        subs_p, subs_t   = get_sub_chapter_progress(conn, u["uid"], ch)
        if complete:
            conn.execute(
                "INSERT OR IGNORE INTO chapter_completions (user_id, chapter) VALUES (?,?)",
                (u["uid"], ch))
            conn.commit()
        result.append({
            "index": i, "name": ch, "total_questions": total_qs,
            "attempted": att, "correct": cor, "accuracy": acc,
            "unlocked": unlocked, "complete": complete,
            "subs_practiced": subs_p, "subs_total": subs_t,
            "unlock_threshold_attempts": UNLOCK_REQUIRED_ATTEMPTS,
            "unlock_threshold_accuracy": UNLOCK_REQUIRED_ACCURACY,
            "status": ("complete" if complete else
                       "weak" if acc < 50 and att > 0 else
                       "moderate" if acc < 75 and att > 0 else
                       "strong" if att > 0 else "untouched"),
        })
    conn.close()
    return {"chapters": result}

# ── CHAPTER LESSON ────────────────────────────────────────────────────────────

@app.get("/api/chapters/{chapter_name}/lesson")
def get_chapter_lesson(chapter_name: str, u=Depends(get_current_user)):
    conn = get_db()
    cached = conn.execute(
        "SELECT content FROM chapter_lessons WHERE user_id=? AND chapter=?",
        (u["uid"], chapter_name)).fetchone()
    if cached:
        conn.close()
        return {"chapter": chapter_name, "content": cached["content"], "cached": True}
    exam_row  = conn.execute("SELECT exam_type FROM users WHERE id=?", (u["uid"],)).fetchone()
    exam_type = exam_row["exam_type"] if exam_row else "General"
    subs = conn.execute(
        "SELECT name FROM sub_chapters WHERE user_id=? AND parent_chapter=? ORDER BY order_index",
        (u["uid"], chapter_name)).fetchall()
    conn.close()
    sub_names   = [s["name"] for s in subs] if subs else []
    web_ctx     = search_topic_context(chapter_name, exam_type, deep=True)
    ctx_block   = f'\nREAL-WORLD REFERENCE:\n"""\n{web_ctx[:5000]}\n"""\n' if web_ctx else ""
    sub_block   = ("\nThis chapter covers: " + ", ".join(sub_names) + ".\n") if sub_names else ""
    style_hints = get_exam_style_hints(exam_type)
    prompt = f"""You are a world-class exam tutor for {exam_type}.{ctx_block}{sub_block}
Create a comprehensive CHAPTER OVERVIEW LESSON for "{chapter_name}".
Use EXACT section headers with ###:
### 🗺️ Chapter Overview
### 🎯 Learning Objectives
### 🧱 Core Pillars
### 📅 Important Facts & Figures
### 🗂️ Sub-Topic Roadmap
### ⚡ Exam Strategy
Question styles:
{style_hints}
### 🔗 Chapter Connections
Style: **bold** key terms, *italics* for numbers/dates/formulas. 700-900 words total.
Return plain text with section headers ONLY — NO JSON."""
    content = groq_chat(prompt, temperature=0.5, json_mode=False)
    conn3 = get_db()
    conn3.execute("INSERT OR REPLACE INTO chapter_lessons (user_id, chapter, content) VALUES (?,?,?)",
        (u["uid"], chapter_name, content))
    conn3.commit(); conn3.close()
    return {"chapter": chapter_name, "content": content, "cached": False, "used_web": bool(web_ctx)}

# ── SUB-CHAPTERS ──────────────────────────────────────────────────────────────

@app.get("/api/chapters/{chapter_name}/sub-chapters")
def get_sub_chapters(chapter_name: str, u=Depends(get_current_user)):
    conn = get_db()
    subs = conn.execute(
        "SELECT * FROM sub_chapters WHERE user_id=? AND parent_chapter=? ORDER BY order_index",
        (u["uid"], chapter_name)).fetchall()
    if not subs:
        exam_row  = conn.execute("SELECT exam_type FROM users WHERE id=?", (u["uid"],)).fetchone()
        exam_type = exam_row["exam_type"] if exam_row else "General"
        prompt = (
            f'For the {exam_type} exam chapter "{chapter_name}", '
            f'create exactly {SUBCHAPTERS_PER_CHAPTER} sub-topics.\n'
            f'Return JSON: {{"sub_chapters": ["Sub-topic 1", ..., "Sub-topic {SUBCHAPTERS_PER_CHAPTER}"]}}\n'
            f'Return ONLY valid JSON.'
        )
        raw = groq_chat(prompt, temperature=0.4)
        data = parse_json(raw)
        sub_names = data.get("sub_chapters", data) if isinstance(data, dict) else data
        if not isinstance(sub_names, list) or len(sub_names) < 1:
            sub_names = [f"{chapter_name} Part {i + 1}" for i in range(SUBCHAPTERS_PER_CHAPTER)]
        for i, name in enumerate(sub_names[:SUBCHAPTERS_PER_CHAPTER]):
            conn.execute(
                "INSERT INTO sub_chapters (user_id,parent_chapter,name,order_index) VALUES (?,?,?,?)",
                (u["uid"], chapter_name, str(name).strip(), i))
        conn.commit()
        subs = conn.execute(
            "SELECT * FROM sub_chapters WHERE user_id=? AND parent_chapter=? ORDER BY order_index",
            (u["uid"], chapter_name)).fetchall()
    result = []
    for sub in subs:
        sq = conn.execute("""
            SELECT COUNT(DISTINCT q.id) AS total,
                   COUNT(DISTINCT CASE WHEN ta.id IS NOT NULL THEN q.id END) AS attempted,
                   COUNT(ta.id) AS total_attempts,
                   SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) AS correct_attempts
            FROM questions q
            LEFT JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
            WHERE q.user_id=? AND q.sub_chapter=?
        """, (u["uid"], u["uid"], sub["name"])).fetchone()
        att     = sq["attempted"]        or 0 if sq else 0
        tot     = sq["total"]            or 0 if sq else 0
        tot_att = sq["total_attempts"]   or 0 if sq else 0
        cor_att = sq["correct_attempts"] or 0 if sq else 0
        acc     = round(cor_att / max(tot_att, 1) * 100, 1) if tot_att > 0 else 0
        practiced = tot_att >= REQUIRED_SUB_ATTEMPTS
        result.append({
            "id": sub["id"], "name": sub["name"], "order_index": sub["order_index"],
            "total_questions": tot, "attempted": att, "accuracy": acc,
            "total_attempts": tot_att, "practiced": practiced,
            "required_attempts": REQUIRED_SUB_ATTEMPTS,
            "can_generate_more": tot < MAX_Q_PER_SUBCHAPTER,
            "max_questions": MAX_Q_PER_SUBCHAPTER,
        })
    conn.close()
    return {"sub_chapters": result, "chapter": chapter_name,
            "required_sub_attempts": REQUIRED_SUB_ATTEMPTS,
            "total_sub_chapters": SUBCHAPTERS_PER_CHAPTER,
            "max_questions_per_sub": MAX_Q_PER_SUBCHAPTER}

# ── CHAPTER COMPLETION CHECK ──────────────────────────────────────────────────

@app.get("/api/chapters/{chapter_name}/completion")
def check_chapter_completion(chapter_name: str, u=Depends(get_current_user)):
    conn = get_db()
    row  = conn.execute("SELECT chapters FROM users WHERE id=?", (u["uid"],)).fetchone()
    if not row or not row["chapters"]:
        conn.close()
        return {"complete": False, "next_unlocked": False}
    chapters = json.loads(row["chapters"])
    try:
        idx = chapters.index(chapter_name)
    except ValueError:
        conn.close()
        raise HTTPException(404, "Chapter not found")
    att, cor, acc  = chapter_stats(conn, u["uid"], chapter_name)
    subs_p, subs_t = get_sub_chapter_progress(conn, u["uid"], chapter_name)
    complete       = is_chapter_complete(conn, u["uid"], chapters, idx)
    next_unlocked  = (idx + 1 < len(chapters) and is_unlocked(conn, u["uid"], chapters, idx + 1))
    next_chapter   = chapters[idx + 1] if idx + 1 < len(chapters) else None
    new_achs = []
    if complete:
        conn.execute("INSERT OR IGNORE INTO chapter_completions (user_id, chapter) VALUES (?,?)",
            (u["uid"], chapter_name))
        conn.commit()
        current_idx = u.get("daily_test_chapter_idx", 0) or 0
        if idx == current_idx and idx + 1 < len(chapters):
            conn.execute("UPDATE users SET daily_test_chapter_idx=? WHERE id=?",
                         (idx + 1, u["uid"]))
            conn.commit()
        new_achs = check_and_award_achievements(
            conn, u["uid"], {"chapter_completed": True, "mode": "completion"})
    conn.close()
    return {
        "chapter": chapter_name, "complete": complete,
        "attempted": att, "accuracy": acc,
        "subs_practiced": subs_p, "subs_total": subs_t,
        "required_attempts": UNLOCK_REQUIRED_ATTEMPTS,
        "required_accuracy": UNLOCK_REQUIRED_ACCURACY,
        "next_chapter": next_chapter, "next_unlocked": next_unlocked,
        "new_achievements": new_achs,
    }

# ── QUESTION GENERATION ───────────────────────────────────────────────────────

@app.post("/api/questions/generate")
def generate_questions(req: GenQuestionsReq, u=Depends(get_current_user)):
    conn      = get_db()
    exam_row  = conn.execute("SELECT exam_type FROM users WHERE id=?", (u["uid"],)).fetchone()
    exam_type = exam_row["exam_type"] if exam_row else "General"
    filter_clause = "AND sub_chapter=?" if req.sub_chapter else "AND (sub_chapter IS NULL OR sub_chapter=?)"
    filter_val    = req.sub_chapter or ""
    existing_cnt = conn.execute(
        f"SELECT COUNT(*) as cnt FROM questions WHERE user_id=? AND chapter=? {filter_clause}",
        (u["uid"], req.chapter, filter_val)).fetchone()["cnt"]
    if existing_cnt >= MAX_Q_PER_SUBCHAPTER:
        qs = conn.execute(
            f"SELECT * FROM questions WHERE user_id=? AND chapter=? {filter_clause} ORDER BY RANDOM() LIMIT ?",
            (u["uid"], req.chapter, filter_val, req.count)).fetchall()
        conn.close()
        return {"questions": [fmt_q(q) for q in qs], "generated": False,
                "total_available": existing_cnt, "max_per_subchapter": MAX_Q_PER_SUBCHAPTER}
    need        = min(req.count, MAX_Q_PER_SUBCHAPTER - existing_cnt, 10)
    topic       = f'{req.sub_chapter} (part of {req.chapter})' if req.sub_chapter else req.chapter
    web_ctx     = search_topic_context(req.sub_chapter or req.chapter, exam_type, deep=True)
    ctx_block   = f'\nREAL-WORLD REFERENCE:\n"""\n{web_ctx[:4000]}\n"""\n' if web_ctx else ""
    style_hints = get_exam_style_hints(exam_type)
    prompt = build_question_prompt(topic, exam_type, need, style_hints, ctx_block, existing_cnt)
    raw    = groq_chat(prompt, temperature=0.75)
    data   = parse_json(raw)
    raw_qs = data.get("questions", data) if isinstance(data, dict) else data
    if not isinstance(raw_qs, list):
        raw_qs = []
    stored = []
    for q in raw_qs:
        vq = validate_question_dict(q)
        if not vq:
            continue
        try:
            conn.execute(
                "INSERT INTO questions (user_id,chapter,sub_chapter,question,options,correct_answer,explanation,difficulty) VALUES (?,?,?,?,?,?,?,?)",
                (u["uid"], req.chapter, req.sub_chapter, vq["question"],
                 json.dumps(vq["options"]), vq["correct_answer"], vq["explanation"], vq["difficulty"]))
            conn.commit()
            qid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            stored.append({"id": qid, "chapter": req.chapter, "sub_chapter": req.sub_chapter, **vq})
        except Exception as exc:
            logger.error("Question insert error: %s", exc)
    if len(stored) < req.count and existing_cnt > 0:
        stored_ids = [q["id"] for q in stored] or [0]
        ph = ",".join("?" * len(stored_ids))
        old_qs = conn.execute(
            f"SELECT * FROM questions WHERE user_id=? AND chapter=? {filter_clause} AND id NOT IN ({ph}) ORDER BY RANDOM() LIMIT ?",
            [u["uid"], req.chapter, filter_val] + stored_ids + [req.count - len(stored)]).fetchall()
        stored = [fmt_q(q) for q in old_qs] + stored
    total_now = conn.execute(
        f"SELECT COUNT(*) FROM questions WHERE user_id=? AND chapter=? {filter_clause}",
        (u["uid"], req.chapter, filter_val)).fetchone()[0]
    conn.close()
    return {"questions": stored[:req.count], "generated": True,
            "total_available": total_now,
            "can_generate_more": total_now < MAX_Q_PER_SUBCHAPTER,
            "max_per_subchapter": MAX_Q_PER_SUBCHAPTER,
            "used_web_search": bool(web_ctx)}

# ── LEARN ─────────────────────────────────────────────────────────────────────

@app.post("/api/learn")
def generate_lesson(req: LearnReq, u=Depends(get_current_user)):
    conn      = get_db()
    exam_row  = conn.execute("SELECT exam_type FROM users WHERE id=?", (u["uid"],)).fetchone()
    exam_type = exam_row["exam_type"] if exam_row else "General"
    conn.close()
    web_ctx     = search_topic_context(req.sub_chapter, exam_type, deep=True)
    ctx_block   = f'\nREAL-WORLD REFERENCE:\n"""\n{web_ctx[:4000]}\n"""\n' if web_ctx else ""
    style_hints = get_exam_style_hints(exam_type)
    prompt = f"""You are a world-class exam tutor for {exam_type}.{ctx_block}
Create a comprehensive exam-focused lesson on "{req.sub_chapter}" (chapter: "{req.chapter}").
Use EXACT section headers with ###:
### 🎯 Key Concepts
### 📋 Important Facts & Data
### 📖 Detailed Explanation
### 🧠 Memory Tricks
### ⚡ Exam Tips & Traps
Question styles: {style_hints}
### 🔗 Connections
600-800 words. Return plain text with section headers ONLY — NO JSON."""
    content = groq_chat(prompt, temperature=0.5, json_mode=False)
    return {"content": content, "chapter": req.chapter, "sub_chapter": req.sub_chapter, "used_web": bool(web_ctx)}

# ── CHAPTER PRACTICE SUBMIT ───────────────────────────────────────────────────

@app.post("/api/test/chapter-practice")
def submit_chapter_practice(req: ChapterPracticeReq, u=Depends(get_current_user)):
    conn  = get_db()
    score = 0
    results = []
    # FIX: Collect coaching triggers for wrong answers
    coaching_triggers = []

    for qid_str, ua in req.answers.items():
        try:
            qid = int(qid_str)
        except (ValueError, TypeError):
            continue
        q = conn.execute("SELECT * FROM questions WHERE id=? AND user_id=?", (qid, u["uid"])).fetchone()
        if not q:
            continue
        q = dict(q)
        ua_int     = safe_answer_int(ua)
        time_t     = int(req.time_taken.get(str(qid), 0) or 0)
        is_correct = 1 if ua_int is not None and ua_int == q["correct_answer"] else 0
        if is_correct:
            score += 1
            award_coins(conn, u["uid"], COINS_PER_CORRECT)
        else:
            # FIX: Build coaching trigger for wrong answer
            coaching_triggers.append(build_coaching_trigger(
                {"id": qid, "question": q["question"],
                 "options": json.loads(q["options"]),
                 "correct_answer": q["correct_answer"],
                 "explanation": q["explanation"],
                 "chapter": q["chapter"]},
                ua_int if ua_int is not None else -1
            ))
        update_consecutive_correct(conn, u["uid"], bool(is_correct))
        conn.execute(
            "INSERT INTO test_attempts (user_id,question_id,user_answer,is_correct,time_taken,session_type) VALUES (?,?,?,?,?,?)",
            (u["uid"], qid, ua_int, is_correct, time_t, req.session_type or "chapter"))
        if time_t > 0:
            conn.execute(
                "INSERT INTO question_latency (user_id,question_id,session_type,time_taken_sec,is_correct,chapter,difficulty) VALUES (?,?,?,?,?,?,?)",
                (u["uid"], qid, req.session_type or "chapter", time_t, is_correct, q["chapter"], q["difficulty"]))
        results.append({
            "question_id": qid, "question": q["question"],
            "options": json.loads(q["options"]), "correct_answer": q["correct_answer"],
            "user_answer": ua_int, "is_correct": bool(is_correct), "explanation": q["explanation"],
        })
    conn.commit()
    total = len(results)
    pct   = round(score / max(total, 1) * 100, 1)
    xp_earned = score * 5 + (25 if pct >= 75 else 10)
    award_xp(conn, u["uid"], xp_earned, "chapter_practice")
    lat_stats = compute_latency_stats(conn, u["uid"], 30)
    lat_alerts = get_latency_alerts(lat_stats)
    new_achs = check_and_award_achievements(conn, u["uid"], {"score": score, "total": total, "mode": "chapter"})
    conn.close()
    return {"score": score, "total": total, "percentage": pct, "results": results,
            "saved": True, "xp_earned": xp_earned, "new_achievements": new_achs,
            "coins_earned": score * COINS_PER_CORRECT,
            "latency_alerts": lat_alerts[:2],
            # FIX: Return coaching triggers so frontend can auto-open coach for wrong answers
            "coaching_triggers": coaching_triggers,
            "has_wrong_answers": len(coaching_triggers) > 0,
            "first_coaching_trigger": coaching_triggers[0] if coaching_triggers else None}

# ── DAILY TEST ────────────────────────────────────────────────────────────────

@app.get("/api/test/daily")
def daily_test(u=Depends(get_current_user)):
    today = date.today().isoformat()
    conn  = get_db()
    uid   = u["uid"]

    existing = conn.execute(
        "SELECT * FROM daily_tests WHERE user_id=? AND test_date=?", (uid, today)).fetchone()
    if existing:
        # FIX: Convert to dict to safely use .get()
        existing = dict(existing)
        q_ids    = json.loads(existing["question_ids"])
        qs       = [fmt_q(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())
                    for qid in q_ids]
        qs = [q for q in qs if q]
        answers = json.loads(existing["answers"]) if existing.get("answers") else {}
        conn.close()
        total = existing.get("total") or len(qs)
        score = existing.get("score") or 0
        return {"test_id": existing["id"], "completed": bool(existing.get("completed")),
                "score": score, "total": total,
                "percentage": round(score / max(total, 1) * 100, 1),
                "questions": qs, "answers": answers,
                "chapter_name": existing.get("chapter_name")}

    chapters_row = conn.execute("SELECT chapters FROM users WHERE id=?", (uid,)).fetchone()
    chapters = json.loads(chapters_row["chapters"]) if chapters_row and chapters_row["chapters"] else []

    active_chapter, active_idx = get_active_daily_chapter(conn, uid, chapters)

    if not active_chapter:
        conn.close()
        return {"test_id": None, "completed": False, "questions": [],
                "message": "Complete exam setup first to get a daily test."}

    answered_today = set(
        r["question_id"] for r in conn.execute(
            "SELECT DISTINCT question_id FROM test_attempts WHERE user_id=? AND date(attempted_at)=?",
            (uid, today)).fetchall()
    )

    new_q_ids = [r["id"] for r in conn.execute("""
        SELECT id FROM questions WHERE user_id=? AND chapter=?
        AND id NOT IN (
            SELECT DISTINCT question_id FROM test_attempts WHERE user_id=?
        )
        ORDER BY RANDOM() LIMIT 15
    """, (uid, active_chapter, uid)).fetchall()
    if r["id"] not in answered_today]

    weak_ids = [r["id"] for r in conn.execute("""
        SELECT q.id, SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) as wrong,
               SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) as right
        FROM questions q
        JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
        WHERE q.user_id=? AND q.chapter=?
        GROUP BY q.id HAVING wrong > right
        ORDER BY wrong DESC LIMIT 10
    """, (uid, uid, active_chapter)).fetchall()
    if r["id"] not in answered_today]

    all_ids = list(dict.fromkeys(new_q_ids + weak_ids))[:20]

    if len(all_ids) < 10:
        extra = [r["id"] for r in conn.execute(
            "SELECT id FROM questions WHERE user_id=? AND chapter=? ORDER BY RANDOM() LIMIT 20",
            (uid, active_chapter)).fetchall()
        if r["id"] not in answered_today and r["id"] not in all_ids]
        all_ids = list(dict.fromkeys(all_ids + extra))[:20]

    if not all_ids:
        for i, ch in enumerate(chapters):
            if is_unlocked(conn, uid, chapters, i):
                fallback = [r["id"] for r in conn.execute(
                    "SELECT id FROM questions WHERE user_id=? AND chapter=? ORDER BY RANDOM() LIMIT 20",
                    (uid, ch)).fetchall()
                if r["id"] not in answered_today]
                all_ids.extend(fallback)
                if len(all_ids) >= 10:
                    break
        all_ids = list(dict.fromkeys(all_ids))[:20]

    if not all_ids:
        conn.close()
        return {"test_id": None, "completed": False, "questions": [],
                "message": f"Generate questions for '{active_chapter}' first to get your daily test.",
                "current_chapter": active_chapter}

    conn.execute(
        "INSERT INTO daily_tests (user_id,test_date,question_ids,total,chapter_name) VALUES (?,?,?,?,?)",
        (uid, today, json.dumps(all_ids), len(all_ids), active_chapter))
    conn.commit()
    test_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    qs = [fmt_q(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()) for qid in all_ids]
    qs = [q for q in qs if q]
    conn.close()
    return {"test_id": test_id, "completed": False, "questions": qs, "total": len(qs),
            "current_chapter": active_chapter, "chapter_index": active_idx,
            "chapter_name": active_chapter}

@app.post("/api/test/submit")
def submit_test(req: SubmitTestReq, u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    # FIX: collect coaching triggers for wrong answers
    coaching_triggers = []

    def _process_answers(q_ids, answers, time_taken, session_type):
        score = 0; results = []
        for qid in q_ids:
            q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
            if not q: continue
            q = dict(q)
            ua_int     = safe_answer_int(answers.get(str(qid)))
            time_t     = int(time_taken.get(str(qid), 0) or 0)
            is_correct = 1 if ua_int is not None and ua_int == q["correct_answer"] else 0
            if is_correct:
                score += 1
                award_coins(conn, uid, COINS_PER_CORRECT)
            else:
                # FIX: Build coaching trigger for wrong answer
                coaching_triggers.append(build_coaching_trigger(
                    {"id": qid, "question": q["question"],
                     "options": json.loads(q["options"]),
                     "correct_answer": q["correct_answer"],
                     "explanation": q["explanation"],
                     "chapter": q["chapter"]},
                    ua_int if ua_int is not None else -1
                ))
            update_consecutive_correct(conn, uid, bool(is_correct))
            conn.execute(
                "INSERT INTO test_attempts (user_id,question_id,user_answer,is_correct,time_taken,session_type) VALUES (?,?,?,?,?,?)",
                (uid, qid, ua_int, is_correct, time_t, session_type))
            if time_t > 0:
                conn.execute(
                    "INSERT INTO question_latency (user_id,question_id,session_type,time_taken_sec,is_correct,chapter,difficulty) VALUES (?,?,?,?,?,?,?)",
                    (uid, qid, session_type, time_t, is_correct, q["chapter"], q["difficulty"]))
            results.append({"question_id": qid, "question": q["question"],
                            "options": json.loads(q["options"]), "correct_answer": q["correct_answer"],
                            "user_answer": ua_int, "is_correct": bool(is_correct),
                            "explanation": q["explanation"], "time_taken": time_t})
        return score, results

    if req.test_id == -1:
        q_ids  = [int(k) for k in req.answers.keys() if k.isdigit() or str(k).lstrip('-').isdigit()]
        score, results = _process_answers(q_ids, req.answers, req.time_taken, req.session_type or "chapter")
        conn.commit()
        xp_earned = score * 5 + 10
        award_xp(conn, uid, xp_earned, "chapter")
        lat_stats  = compute_latency_stats(conn, uid, 30)
        lat_alerts = get_latency_alerts(lat_stats)
        new_achs = check_and_award_achievements(conn, uid, {"score": score, "total": len(results), "mode": "chapter"})
        conn.close()
        return {"score": score, "total": len(results),
                "percentage": round(score / max(len(results), 1) * 100, 1),
                "results": results, "xp_earned": xp_earned, "new_achievements": new_achs,
                "coins_earned": score * COINS_PER_CORRECT, "latency_alerts": lat_alerts[:2],
                "coaching_triggers": coaching_triggers,
                "has_wrong_answers": len(coaching_triggers) > 0,
                "first_coaching_trigger": coaching_triggers[0] if coaching_triggers else None}

    # FIX: Convert sqlite3.Row to dict immediately after fetch to allow .get() calls
    test_row = conn.execute("SELECT * FROM daily_tests WHERE id=? AND user_id=?",
                        (req.test_id, uid)).fetchone()
    if not test_row:
        conn.close()
        raise HTTPException(404, "Test not found")
    # FIX: Always convert to dict — sqlite3.Row does NOT support .get()
    test = dict(test_row)

    q_ids = json.loads(test["question_ids"])
    if test["completed"]:
        results = []
        saved_answers = json.loads(test["answers"]) if test.get("answers") else {}
        for qid in q_ids:
            q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
            if not q: continue
            q = dict(q)
            ua_int = safe_answer_int(saved_answers.get(str(qid)))
            results.append({"question_id": qid, "question": q["question"],
                            "options": json.loads(q["options"]), "correct_answer": q["correct_answer"],
                            "user_answer": ua_int,
                            "is_correct": ua_int is not None and ua_int == q["correct_answer"],
                            "explanation": q["explanation"]})
        conn.close()
        return {"score": test.get("score") or 0, "total": test.get("total") or len(q_ids),
                "percentage": round((test.get("score") or 0) / max(test.get("total") or 1, 1) * 100, 1),
                "results": results, "already_submitted": True,
                "chapter_name": test.get("chapter_name")}

    score, results = _process_answers(q_ids, req.answers, req.time_taken, req.session_type or "daily")
    conn.execute(
        "UPDATE daily_tests SET completed=1,score=?,answers=?,total=?,completed_at=? WHERE id=?",
        (score, json.dumps({str(k): v for k, v in req.answers.items()}),
         len(q_ids), datetime.now().isoformat(), req.test_id))
    conn.commit()
    xp_earned = score * 10 + (50 if score / max(len(q_ids), 1) >= 0.75 else 20)
    award_xp(conn, uid, xp_earned, "daily_test")
    prev = conn.execute(
        "SELECT score, total FROM daily_tests WHERE user_id=? AND completed=1 AND id!=? ORDER BY id DESC LIMIT 1",
        (uid, req.test_id)).fetchone()
    prev_pct = round((prev["score"] or 0) / max(prev["total"] or 1, 1) * 100, 1) if prev else 0

    # Check if chapter is now complete → auto-advance
    # FIX: use test.get() safely since test is now a dict
    chapter_name = test.get("chapter_name")
    chapters_row = conn.execute("SELECT chapters FROM users WHERE id=?", (uid,)).fetchone()
    chapters = json.loads(chapters_row["chapters"]) if chapters_row and chapters_row["chapters"] else []
    if chapter_name and chapter_name in chapters:
        ch_idx = chapters.index(chapter_name)
        if is_chapter_complete(conn, uid, chapters, ch_idx):
            conn.execute("INSERT OR IGNORE INTO chapter_completions (user_id,chapter) VALUES (?,?)",
                         (uid, chapter_name))
            current_idx = u.get("daily_test_chapter_idx", 0) or 0
            if ch_idx == current_idx and ch_idx + 1 < len(chapters):
                conn.execute("UPDATE users SET daily_test_chapter_idx=? WHERE id=?",
                             (ch_idx + 1, uid))
            conn.commit()

    lat_stats  = compute_latency_stats(conn, uid, 30)
    lat_alerts = get_latency_alerts(lat_stats)
    new_achs = check_and_award_achievements(conn, uid, {
        "score": score, "total": len(q_ids), "mode": "daily", "prev_pct": prev_pct,
    })
    conn.close()
    return {"score": score, "total": len(q_ids),
            "percentage": round(score / max(len(q_ids), 1) * 100, 1),
            "results": results, "xp_earned": xp_earned, "new_achievements": new_achs,
            "coins_earned": score * COINS_PER_CORRECT,
            "chapter_name": chapter_name,
            "latency_alerts": lat_alerts[:2],
            # FIX: Return coaching triggers so frontend can auto-open coach after wrong answers
            "coaching_triggers": coaching_triggers,
            "has_wrong_answers": len(coaching_triggers) > 0,
            "first_coaching_trigger": coaching_triggers[0] if coaching_triggers else None}

# ── WEAK SESSION ──────────────────────────────────────────────────────────────

@app.get("/api/test/weak-session")
def get_weak_session(chapter: Optional[str] = None, u=Depends(get_current_user)):
    conn = get_db()

    # FIX: Don't reuse old incomplete sessions — always create a fresh one
    # (old sessions caused repeated correct-answer questions to re-appear)
    # Mark any stale incomplete sessions as completed first
    conn.execute(
        "UPDATE weak_sessions SET completed=1 WHERE user_id=? AND completed=0",
        (u["uid"],))
    conn.commit()

    chapter_filter = "AND q.chapter = ?" if chapter else ""

    # FIX: Only include questions where the MOST RECENT attempt is WRONG.
    # This ensures questions the user has since gotten correct are excluded.
    weak_qs = conn.execute(f"""
        SELECT q.id, q.chapter, q.sub_chapter,
               SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) AS wrong_count,
               SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) AS right_count,
               MAX(ta.attempted_at) AS last_attempted,
               (SELECT ta2.is_correct FROM test_attempts ta2
                WHERE ta2.question_id=q.id AND ta2.user_id=?
                ORDER BY ta2.attempted_at DESC LIMIT 1) AS last_result
        FROM questions q
        JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
        WHERE q.user_id=? {chapter_filter}
        GROUP BY q.id
        HAVING last_result = 0
        ORDER BY wrong_count DESC LIMIT 15
    """, [u["uid"], u["uid"], u["uid"]] + ([chapter] if chapter else [])).fetchall()

    if not weak_qs or len(weak_qs) < 3:
        # Fallback: questions with more wrong than right, excluding recently-correct
        weak_qs = conn.execute(f"""
            SELECT q.id, q.chapter, q.sub_chapter,
                   SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) AS wrong_count,
                   SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) AS right_count,
                   (SELECT ta2.is_correct FROM test_attempts ta2
                    WHERE ta2.question_id=q.id AND ta2.user_id=?
                    ORDER BY ta2.attempted_at DESC LIMIT 1) AS last_result
            FROM questions q
            JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
            WHERE q.user_id=? {chapter_filter} AND ta.is_correct=0
            GROUP BY q.id
            HAVING wrong_count > COALESCE(right_count, 0) AND last_result = 0
            ORDER BY wrong_count DESC LIMIT 15
        """, [u["uid"], u["uid"], u["uid"]] + ([chapter] if chapter else [])).fetchall()

    if not weak_qs:
        conn.close()
        return {"session_id": None, "questions": [],
                "message": "No weak areas found! All practiced questions are mastered. 🎉"}

    q_ids = [r["id"] for r in weak_qs]
    conn.execute("INSERT INTO weak_sessions (user_id,question_ids,total) VALUES (?,?,?)",
                 (u["uid"], json.dumps(q_ids), len(q_ids)))
    conn.commit()
    session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    qs = [fmt_q(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()) for qid in q_ids]
    qs = [q for q in qs if q]
    conn.close()
    return {"session_id": session_id, "questions": qs, "total": len(qs)}

@app.post("/api/test/weak-session/submit")
def submit_weak_session(req: WeakSessionSubmitReq, u=Depends(get_current_user)):
    conn    = get_db()
    session = conn.execute("SELECT * FROM weak_sessions WHERE id=? AND user_id=?",
                           (req.session_id, u["uid"])).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Session not found")
    # FIX: Convert to dict for safe .get() access
    session = dict(session)
    if session["completed"]:
        conn.close()
        return {"score": session.get("score") or 0, "total": session.get("total") or 0,
                "percentage": round((session.get("score") or 0) / max(session.get("total") or 1, 1) * 100, 1),
                "results": [], "already_submitted": True}
    q_ids = json.loads(session["question_ids"])
    score = 0; results = []
    # FIX: collect coaching triggers for wrong answers in weak session
    coaching_triggers = []
    for qid in q_ids:
        q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if not q: continue
        q = dict(q)
        ua_int     = safe_answer_int(req.answers.get(str(qid)))
        time_t     = int(req.time_taken.get(str(qid), 0) or 0)
        is_correct = 1 if ua_int is not None and ua_int == q["correct_answer"] else 0
        if is_correct:
            score += 1
            award_coins(conn, u["uid"], COINS_PER_CORRECT)
        else:
            coaching_triggers.append(build_coaching_trigger(
                {"id": qid, "question": q["question"],
                 "options": json.loads(q["options"]),
                 "correct_answer": q["correct_answer"],
                 "explanation": q["explanation"],
                 "chapter": q["chapter"]},
                ua_int if ua_int is not None else -1
            ))
        update_consecutive_correct(conn, u["uid"], bool(is_correct))
        conn.execute(
            "INSERT INTO test_attempts (user_id,question_id,user_answer,is_correct,time_taken,session_type) VALUES (?,?,?,?,?,?)",
            (u["uid"], qid, ua_int, is_correct, time_t, "weak"))
        if time_t > 0:
            conn.execute(
                "INSERT INTO question_latency (user_id,question_id,session_type,time_taken_sec,is_correct,chapter,difficulty) VALUES (?,?,?,?,?,?,?)",
                (u["uid"], qid, "weak", time_t, is_correct, q["chapter"], q["difficulty"]))
        results.append({"question_id": qid, "question": q["question"],
                        "options": json.loads(q["options"]), "correct_answer": q["correct_answer"],
                        "user_answer": ua_int, "is_correct": bool(is_correct), "explanation": q["explanation"]})
    conn.execute("UPDATE weak_sessions SET completed=1,score=?,answers=?,total=? WHERE id=?",
                 (score, json.dumps({str(k): v for k, v in req.answers.items()}),
                  len(q_ids), req.session_id))
    conn.commit()
    xp_earned = score * 8 + 20
    award_xp(conn, u["uid"], xp_earned, "weak_session")
    lat_stats  = compute_latency_stats(conn, u["uid"], 30)
    lat_alerts = get_latency_alerts(lat_stats)
    new_achs = check_and_award_achievements(conn, u["uid"], {"score": score, "total": len(q_ids), "mode": "weak"})
    conn.close()
    return {"score": score, "total": len(q_ids),
            "percentage": round(score / max(len(q_ids), 1) * 100, 1),
            "results": results, "improved": score, "xp_earned": xp_earned,
            "new_achievements": new_achs, "coins_earned": score * COINS_PER_CORRECT,
            "latency_alerts": lat_alerts[:2],
            # FIX: coaching triggers for any remaining wrong answers
            "coaching_triggers": coaching_triggers,
            "has_wrong_answers": len(coaching_triggers) > 0,
            "first_coaching_trigger": coaching_triggers[0] if coaching_triggers else None}

# ── DECISION LATENCY TRACKER ──────────────────────────────────────────────────

@app.get("/api/latency/stats")
def get_latency_stats(days: int = Query(30, ge=1, le=90), u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    stats = compute_latency_stats(conn, uid, days)
    alerts = get_latency_alerts(stats)

    sessions = conn.execute("""
        SELECT session_type, COUNT(*) as q_count,
               AVG(time_taken_sec) as avg_t,
               MAX(time_taken_sec) as max_t,
               MIN(time_taken_sec) as min_t,
               SUM(CASE WHEN time_taken_sec > ? THEN 1 ELSE 0 END) as slow_q,
               date(recorded_at) as day
        FROM question_latency
        WHERE user_id=? AND time_taken_sec > 0 AND recorded_at >= ?
        GROUP BY date(recorded_at), session_type
        ORDER BY recorded_at DESC LIMIT 14
    """, (LATENCY_SLOW_SINGLE, uid,
          (datetime.now() - timedelta(days=days)).isoformat())).fetchall()

    diff_lat = conn.execute("""
        SELECT difficulty, AVG(time_taken_sec) as avg_t, COUNT(*) as cnt
        FROM question_latency
        WHERE user_id=? AND time_taken_sec > 0
        GROUP BY difficulty
    """, (uid,)).fetchall()

    conn.close()
    return {
        "stats": stats,
        "alerts": alerts,
        "thresholds": {
            "fast": LATENCY_FAST_THRESHOLD,
            "slow": LATENCY_SLOW_SINGLE,
            "target_avg": LATENCY_WARNING_THRESHOLD,
        },
        "session_history": [
            {"day": r["day"], "session_type": r["session_type"],
             "questions": r["q_count"], "avg_time": round(r["avg_t"], 1),
             "max_time": r["max_t"], "min_time": r["min_t"],
             "slow_questions": r["slow_q"]}
            for r in sessions
        ],
        "by_difficulty": [
            {"difficulty": r["difficulty"], "avg_time": round(r["avg_t"], 1),
             "question_count": r["cnt"]}
            for r in diff_lat
        ],
    }

@app.post("/api/latency/coach-advice")
def latency_coach_advice(req: LatencyCoachReq, u=Depends(get_current_user)):
    conn  = get_db()
    uid   = u["uid"]
    stats = compute_latency_stats(conn, uid, 30)
    alerts = get_latency_alerts(stats)
    memory = _get_coach_memory(conn, uid)

    chapter_times = stats.get("chapter_breakdown", {})
    slowest_ch = stats.get("slowest_chapter", "unknown chapter")
    top3_slow = sorted(chapter_times.items(), key=lambda x: x[1], reverse=True)[:3]
    slow_ch_str = ", ".join(f"{ch} ({t}s avg)" for ch, t in top3_slow) if top3_slow else "none identified"

    focus = req.chapter or slowest_ch or "exam topics"

    prompt = f"""You are ExamAI Time Coach for {u.get('name','Student')} ({u.get('exam_type','General')}).

THEIR DECISION-LATENCY DATA:
- Average time per question: {stats.get('avg_time', 0)}s  (target: <{LATENCY_WARNING_THRESHOLD}s)
- Slow questions (>{LATENCY_SLOW_SINGLE}s): {stats.get('slow_count', 0)} out of {stats.get('total_tracked', 0)}
- Speed trend: {stats.get('trend', 'unknown')}
- Recent avg: {stats.get('recent_avg', 0)}s vs previous {stats.get('prev_avg', 0)}s
- Slowest chapters: {slow_ch_str}
- Focus area requested: {focus}
{f'What I know about them: {memory[:200]}' if memory else ''}

ALERTS TRIGGERED: {[a['message'] for a in alerts[:2]]}

Write a SHORT, personalized time-efficiency coaching message (4-5 sentences):
1. Acknowledge their specific timing pattern (use actual numbers)
2. Give ONE concrete technique for faster decision-making in "{focus}"
3. Give ONE drill they can do RIGHT NOW (specific, time-boxed: e.g. "Do 10 questions in 8 minutes")
4. End with an encouraging note about their improvement potential.

Tone: direct, energetic, coach-like. NO generic advice."""

    advice = groq_chat(prompt, temperature=0.75, json_mode=False)

    conn.execute(
        "INSERT INTO coach_messages (user_id,message,msg_type) VALUES (?,?,'coach')",
        (uid, advice))
    conn.commit()
    conn.close()

    return {
        "advice": advice,
        "stats_summary": {
            "avg_time": stats.get("avg_time", 0),
            "trend": stats.get("trend", "no_data"),
            "slow_count": stats.get("slow_count", 0),
            "total_tracked": stats.get("total_tracked", 0),
            "slowest_chapter": slowest_ch,
        },
        "alerts": alerts,
        "drill": f"Try: answer 10 questions on '{focus}' with a strict 8-minute timer.",
    }

@app.get("/api/latency/leaderboard")
def latency_leaderboard(u=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("""
        SELECT ql.user_id, u.name, u.avatar_color,
               AVG(ql.time_taken_sec) as avg_t, COUNT(*) as q_cnt
        FROM question_latency ql
        JOIN users u ON ql.user_id = u.id
        WHERE ql.time_taken_sec > 0
        GROUP BY ql.user_id
        HAVING q_cnt >= 20
        ORDER BY avg_t ASC
        LIMIT 20
    """).fetchall()

    my_rank = None
    for i, r in enumerate(rows):
        if r["user_id"] == u["uid"]:
            my_rank = i + 1
            break

    my_stats = compute_latency_stats(conn, u["uid"], 30)
    conn.close()

    return {
        "leaderboard": [
            {"rank": i + 1, "name": r["name"],
             "avatar_color": r["avatar_color"],
             "avg_time_sec": round(r["avg_t"], 1),
             "questions_tracked": r["q_cnt"],
             "is_me": r["user_id"] == u["uid"]}
            for i, r in enumerate(rows)
        ],
        "my_rank": my_rank,
        "my_avg_time": my_stats.get("avg_time", 0),
        "my_questions_tracked": my_stats.get("total_tracked", 0),
        "note": "Rankings based on average decision time (lower is better). Minimum 20 tracked questions to appear.",
    }

# ── GLOBAL TEST (USER-FACING) ─────────────────────────────────────────────────

def _parse_dt(dt_str: str) -> datetime:
    dt_str = (dt_str or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        dt = datetime.fromisoformat(dt_str.split("+")[0].split("-")[0] if "T" not in dt_str else dt_str[:19])
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    else:
        dt = dt.astimezone(pytz.utc)
    return dt


@app.get("/api/global-tests")
def list_global_tests_for_user(u=Depends(get_current_user)):
    conn = get_db()
    exam_type = (u.get("exam_type") or "").strip()

    # FIX: Include 'draft' status so admin-created tests are visible to users.
    # Also use a broader status filter so no tests are accidentally hidden.
    VISIBLE_STATUSES = ('draft', 'generating', 'ready', 'lobby', 'live', 'ended')
    placeholders = ",".join("?" * len(VISIBLE_STATUSES))

    if exam_type:
        rows = conn.execute(
            f"SELECT gt.*, "
            "(SELECT COUNT(*) FROM global_participants WHERE global_test_id=gt.id) as participant_count "
            f"FROM global_tests gt "
            f"WHERE gt.status IN ({placeholders}) "
            "AND (UPPER(gt.exam_type) LIKE ? OR UPPER(gt.exam_type)='GENERAL') "
            "ORDER BY gt.id DESC LIMIT 20",
            list(VISIBLE_STATUSES) + [f"%{exam_type.upper()}%"]
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT gt.*, "
            "(SELECT COUNT(*) FROM global_participants WHERE global_test_id=gt.id) as participant_count "
            f"FROM global_tests gt "
            f"WHERE gt.status IN ({placeholders}) "
            "ORDER BY gt.id DESC LIMIT 20",
            list(VISIBLE_STATUSES)
        ).fetchall()

    uid = u["uid"]
    result = []
    for r in rows:
        # FIX: Convert row to dict for safe access
        r = dict(r)
        part = conn.execute(
            "SELECT * FROM global_participants WHERE global_test_id=? AND user_id=?",
            (r["id"], uid)).fetchone()
        part = dict(part) if part else None

        # FIX: Safely parse question_ids
        try:
            q_count = len(json.loads(r.get("question_ids") or "[]"))
        except Exception:
            q_count = 0

        result.append({
            "id": r["id"],
            "title": r.get("title", ""),
            "exam_type": r.get("exam_type", ""),
            "topic": r.get("topic", ""),
            "status": r.get("status", "draft"),
            "scheduled_at": r.get("scheduled_at"),
            "starts_at": r.get("starts_at"),
            "duration_minutes": r.get("duration_minutes", 60),
            "participant_count": r.get("participant_count", 0),
            "question_count": q_count,
            "winner_name": r.get("winner_name"),
            "winner_score": r.get("winner_score"),
            "my_participation": {
                "joined": part is not None,
                "submitted": part["submitted_at"] is not None if part else False,
                "score": part["score"] if part else None,
                "rank": part["rank"] if part else None,
            } if part else {"joined": False, "submitted": False}
        })
    conn.close()
    # FIX: Always return a valid structure — never let 'tests' be undefined
    return {"tests": result, "total": len(result)}


@app.post("/api/global-tests/join")
def join_global_test(req: GlobalTestJoinReq, u=Depends(get_current_user)):
    conn = get_db()
    test = conn.execute("SELECT * FROM global_tests WHERE id=?", (req.global_test_id,)).fetchone()
    if not test:
        conn.close()
        raise HTTPException(404, "Test not found")
    # FIX: Allow joining draft/ready/lobby/live tests
    test = dict(test)
    if test["status"] not in ("draft", "ready", "lobby", "live"):
        conn.close()
        raise HTTPException(400, f"Test is not open for participation. Status: {test['status']}")

    existing = conn.execute(
        "SELECT id, started_at FROM global_participants WHERE global_test_id=? AND user_id=?",
        (req.global_test_id, u["uid"])).fetchone()
    now_iso = datetime.now().isoformat()

    if existing:
        existing = dict(existing)
        if test["status"] == "live" and not existing["started_at"]:
            conn.execute(
                "UPDATE global_participants SET started_at=? WHERE global_test_id=? AND user_id=?",
                (now_iso, req.global_test_id, u["uid"]))
            conn.commit()
        conn.close()
        return {"message": "Already joined", "already_joined": True,
                "starts_at": test["starts_at"], "duration_minutes": test["duration_minutes"]}

    started_at = now_iso if test["status"] == "live" else None
    conn.execute(
        "INSERT INTO global_participants (global_test_id, user_id, joined_at, started_at) VALUES (?,?,?,?)",
        (req.global_test_id, u["uid"], now_iso, started_at))
    conn.commit()
    conn.close()
    return {
        "message": "Joined! Wait for the test to go live." if test["status"] != "live" else "Joined! Test is live — good luck!",
        "starts_at": test["starts_at"],
        "lobby_opens_at": test["scheduled_at"],
        "duration_minutes": test["duration_minutes"],
        "test_is_live": test["status"] == "live",
    }


@app.get("/api/global-tests/{test_id}/questions")
def get_global_test_questions(test_id: int, u=Depends(get_current_user)):
    conn = get_db()
    test_row = conn.execute("SELECT * FROM global_tests WHERE id=?", (test_id,)).fetchone()
    if not test_row:
        conn.close()
        raise HTTPException(404, "Test not found")
    # FIX: Convert to dict for safe access
    test = dict(test_row)

    # FIX: Allow fetching questions for live OR draft tests (draft = admin testing)
    if test["status"] not in ("live", "draft"):
        conn.close()
        raise HTTPException(400, f"Test is not available. Current status: '{test['status']}'.")

    now_iso = datetime.now().isoformat()
    part = conn.execute(
        "SELECT * FROM global_participants WHERE global_test_id=? AND user_id=?",
        (test_id, u["uid"])).fetchone()

    if not part:
        conn.execute(
            "INSERT INTO global_participants (global_test_id, user_id, joined_at, started_at) VALUES (?,?,?,?)",
            (test_id, u["uid"], now_iso, now_iso))
        conn.commit()
        part = conn.execute(
            "SELECT * FROM global_participants WHERE global_test_id=? AND user_id=?",
            (test_id, u["uid"])).fetchone()
    part = dict(part)

    if part.get("submitted_at"):
        conn.close()
        raise HTTPException(400, "You have already submitted this test.")

    if not part.get("started_at"):
        conn.execute(
            "UPDATE global_participants SET started_at=? WHERE global_test_id=? AND user_id=?",
            (now_iso, test_id, u["uid"]))
        conn.commit()

    qs = conn.execute(
        "SELECT id, question, options, difficulty, topic_tag "
        "FROM global_questions WHERE global_test_id=? ORDER BY id",
        (test_id,)).fetchall()

    # FIX: Gracefully handle time calculation errors
    try:
        starts_dt = _parse_dt(test["starts_at"])
        ends_dt   = starts_dt + timedelta(minutes=test["duration_minutes"])
        now_utc   = datetime.now(pytz.utc)
        seconds_remaining = max(0, int((ends_dt - now_utc).total_seconds()))
        ends_at_iso = ends_dt.isoformat()
    except Exception as exc:
        logger.error("Time calculation error for test %d: %s", test_id, exc)
        seconds_remaining = test["duration_minutes"] * 60
        ends_at_iso = None

    conn.close()

    # FIX: Ensure questions list is always defined (never undefined in JS)
    questions_list = [
        {"id": q["id"], "question": q["question"],
         "options": json.loads(q["options"]) if isinstance(q["options"], str) else (q["options"] or []),
         "difficulty": q["difficulty"] or "medium",
         "topic_tag": q["topic_tag"]}
        for q in qs
    ]

    return {
        "test_id": test_id,
        "title": test.get("title", ""),
        "duration_minutes": test.get("duration_minutes", 60),
        "seconds_remaining": seconds_remaining,
        "ends_at": ends_at_iso,
        "status": test.get("status"),
        "questions": questions_list,
        "total_questions": len(questions_list),
    }


@app.post("/api/global-tests/submit")
def submit_global_test(req: GlobalTestSubmitReq, u=Depends(get_current_user)):
    conn = get_db()
    test_row = conn.execute("SELECT * FROM global_tests WHERE id=?", (req.global_test_id,)).fetchone()
    if not test_row:
        conn.close()
        raise HTTPException(404, "Test not found")
    # FIX: Convert to dict
    test = dict(test_row)
    if test["status"] not in ("live", "ended", "draft"):
        conn.close()
        raise HTTPException(400, f"Test is not accepting submissions. Status: {test['status']}")

    part = conn.execute(
        "SELECT * FROM global_participants WHERE global_test_id=? AND user_id=?",
        (req.global_test_id, u["uid"])).fetchone()
    if not part:
        now_iso = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO global_participants (global_test_id, user_id, joined_at, started_at) VALUES (?,?,?,?)",
            (req.global_test_id, u["uid"], now_iso, now_iso))
        conn.commit()
        part = conn.execute(
            "SELECT * FROM global_participants WHERE global_test_id=? AND user_id=?",
            (req.global_test_id, u["uid"])).fetchone()
    part = dict(part)

    if part.get("submitted_at"):
        conn.close()
        return {"already_submitted": True, "score": part["score"], "total": part["total"],
                "percentage": round((part["score"] or 0) / max(part["total"] or 1, 1) * 100, 1),
                "rank": part.get("rank")}

    qs = conn.execute(
        "SELECT id, correct_answer, explanation FROM global_questions WHERE global_test_id=?",
        (req.global_test_id,)).fetchall()
    score = 0; total = len(qs); results = []
    for q in qs:
        q = dict(q)
        user_ans = req.answers.get(str(q["id"]))
        try:
            user_ans_int = int(user_ans) if user_ans is not None else None
        except (TypeError, ValueError):
            user_ans_int = None
        is_correct = user_ans_int is not None and user_ans_int == q["correct_answer"]
        if is_correct:
            score += 1
        results.append({"question_id": q["id"], "user_answer": user_ans_int,
                        "correct_answer": q["correct_answer"], "is_correct": is_correct,
                        "explanation": q["explanation"]})

    conn.execute(
        "UPDATE global_participants SET submitted_at=?, answers=?, score=?, total=?, time_taken_sec=? "
        "WHERE global_test_id=? AND user_id=?",
        (datetime.now().isoformat(), json.dumps({str(k): v for k, v in req.answers.items()}),
         score, total, req.time_taken_sec, req.global_test_id, u["uid"]))
    conn.commit()

    pct = round(score / max(total, 1) * 100, 1)
    xp_earned = score * 12 + (100 if pct >= 75 else 30)
    award_xp(conn, u["uid"], xp_earned, "global_test")
    if score > 0:
        award_coins(conn, u["uid"], score * 3)
    new_achs = check_and_award_achievements(conn, u["uid"], {"score": score, "total": total, "mode": "global"})
    current_rank = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE global_test_id=? AND submitted_at IS NOT NULL AND score > ?",
        (req.global_test_id, score)).fetchone()[0] + 1
    conn.close()
    return {"score": score, "total": total, "percentage": pct,
            "provisional_rank": current_rank, "xp_earned": xp_earned,
            "coins_earned": score * 3, "results": results, "new_achievements": new_achs,
            "message": f"Submitted! You scored {score}/{total} ({pct}%). Provisional rank: #{current_rank}"}


@app.get("/api/global-tests/{test_id}/leaderboard")
def global_test_leaderboard(test_id: int, u=Depends(get_current_user)):
    conn = get_db()
    test_row = conn.execute("SELECT * FROM global_tests WHERE id=?", (test_id,)).fetchone()
    if not test_row:
        conn.close()
        raise HTTPException(404, "Test not found")
    test = dict(test_row)
    rows = conn.execute(
        "SELECT gp.rank, gp.score, gp.total, gp.time_taken_sec, gp.submitted_at, "
        "u.name, u.avatar_color FROM global_participants gp "
        "JOIN users u ON gp.user_id=u.id WHERE gp.global_test_id=? "
        "AND gp.submitted_at IS NOT NULL "
        "ORDER BY gp.rank ASC, gp.score DESC, gp.time_taken_sec ASC LIMIT 50",
        (test_id,)).fetchall()
    my_part = conn.execute(
        "SELECT rank, score, total, time_taken_sec FROM global_participants WHERE global_test_id=? AND user_id=?",
        (test_id, u["uid"])).fetchone()
    total_p = conn.execute("SELECT COUNT(*) FROM global_participants WHERE global_test_id=?", (test_id,)).fetchone()[0]
    submitted_c = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE global_test_id=? AND submitted_at IS NOT NULL",
        (test_id,)).fetchone()[0]
    conn.close()
    return {
        "test": {"id": test["id"], "title": test.get("title", ""),
                 "exam_type": test.get("exam_type", ""),
                 "status": test.get("status", ""),
                 "winner_name": test.get("winner_name"),
                 "winner_score": test.get("winner_score")},
        "leaderboard": [{"rank": r["rank"] or "—", "name": r["name"],
                         "avatar_color": r["avatar_color"], "score": r["score"],
                         "total": r["total"],
                         "pct": round((r["score"] or 0) / max(r["total"] or 1, 1) * 100, 1),
                         "time_taken_sec": r["time_taken_sec"]} for r in rows],
        "my_result": dict(my_part) if my_part else None,
        "total_participants": total_p,
        "submitted_count": submitted_c,
    }


@app.get("/api/global-tests/{test_id}/status")
def global_test_status(test_id: int, u=Depends(get_current_user)):
    conn = get_db()
    test_row = conn.execute(
        "SELECT id, title, status, scheduled_at, starts_at, duration_minutes, winner_name, winner_score "
        "FROM global_tests WHERE id=?", (test_id,)).fetchone()
    if not test_row:
        conn.close()
        raise HTTPException(404, "Test not found")
    test = dict(test_row)
    part_row = conn.execute(
        "SELECT joined_at, submitted_at, score, rank FROM global_participants WHERE global_test_id=? AND user_id=?",
        (test_id, u["uid"])).fetchone()
    part = dict(part_row) if part_row else None
    participant_count = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE global_test_id=?", (test_id,)).fetchone()[0]
    try:
        starts_dt = _parse_dt(test["starts_at"])
        ends_dt   = starts_dt + timedelta(minutes=test["duration_minutes"])
        now_utc   = datetime.now(pytz.utc)
        seconds_to_start  = max(0, int((starts_dt - now_utc).total_seconds()))
        seconds_remaining = max(0, int((ends_dt   - now_utc).total_seconds()))
    except Exception as exc:
        logger.error("Time calc error test %d: %s", test_id, exc)
        seconds_to_start = seconds_remaining = 0
    conn.close()
    return {
        "test_id": test_id,
        "title": test.get("title", ""),
        "status": test.get("status", ""),
        "scheduled_at": test.get("scheduled_at"),
        "starts_at": test.get("starts_at"),
        "seconds_to_start": seconds_to_start,
        "seconds_remaining": seconds_remaining if test.get("status") == "live" else None,
        "participant_count": participant_count,
        "winner_name": test.get("winner_name"),
        "winner_score": test.get("winner_score"),
        "my_status": {"joined": part is not None,
                      "submitted": part["submitted_at"] is not None if part else False,
                      "score": part["score"] if part else None,
                      "rank": part["rank"] if part else None} if part else {"joined": False},
    }

# ── DYNAMIC SESSION ───────────────────────────────────────────────────────────

def _build_dynamic_prompt(exam_type, chapter, seen_count, web_ctx):
    topic       = chapter or exam_type
    ctx_block   = f'\nREAL-WORLD REFERENCE:\n"""\n{web_ctx[:4000]}\n"""\n' if web_ctx else ""
    style_hints = get_exam_style_hints(exam_type)
    return build_question_prompt(topic, exam_type, DYNAMIC_MAX_PER_CALL,
                                  style_hints, ctx_block, seen_count)

def _generate_dynamic_batch(exam_type, chapter, seen_hashes):
    web_ctx = search_topic_context(chapter or exam_type, exam_type, deep=False)
    prompt  = _build_dynamic_prompt(exam_type, chapter, len(seen_hashes), web_ctx)
    try:
        raw  = groq_chat(prompt, temperature=0.8)
        data = parse_json(raw)
        qs   = data.get("questions", []) if isinstance(data, dict) else data
    except Exception as exc:
        logger.error("Dynamic batch generation failed: %s", exc)
        return []
    results = []
    for q in (qs or []):
        vq = validate_question_dict(q)
        if not vq:
            continue
        h = q_hash(vq["question"])
        if h not in seen_hashes:
            results.append({**vq, "source": "web_ai" if web_ctx else "ai", "hash": h})
    return results

@app.post("/api/session/dynamic/start")
def dynamic_start(req: DynamicStartReq, u=Depends(get_current_user)):
    conn = get_db()
    conn.execute(
        "UPDATE dynamic_sessions SET is_active=0, ended_at=? WHERE user_id=? AND is_active=1",
        (datetime.now().isoformat(), u["uid"]))
    exam_row  = conn.execute("SELECT exam_type FROM users WHERE id=?", (u["uid"],)).fetchone()
    exam_type = exam_row["exam_type"] if exam_row else "General"
    initial_pool = _generate_dynamic_batch(exam_type, req.chapter, set())
    seen = {q["hash"] for q in initial_pool}
    conn.execute(
        "INSERT INTO dynamic_sessions (user_id, chapter, exam_type, seen_hashes, question_pool) VALUES (?,?,?,?,?)",
        (u["uid"], req.chapter, exam_type, json.dumps(list(seen)), json.dumps(initial_pool)))
    conn.commit()
    session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"session_id": session_id, "chapter": req.chapter, "exam_type": exam_type,
            "pool_ready": len(initial_pool),
            "message": f"Dynamic session started! {len(initial_pool)} questions pre-loaded."}

@app.get("/api/session/dynamic/{session_id}/next")
def dynamic_next(session_id: int, u=Depends(get_current_user)):
    conn    = get_db()
    session = conn.execute(
        "SELECT * FROM dynamic_sessions WHERE id=? AND user_id=?",
        (session_id, u["uid"])).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Dynamic session not found")
    if not session["is_active"]:
        conn.close()
        raise HTTPException(400, "Session has already ended. Start a new one.")
    seen_hashes   = set(json.loads(session["seen_hashes"] or "[]"))
    question_pool = json.loads(session["question_pool"] or "[]")
    exam_type     = session["exam_type"] or "General"
    chapter       = session["chapter"]
    q_data = None
    if question_pool:
        q_data = question_pool.pop(0)
    if len(question_pool) < 2:
        new_batch = _generate_dynamic_batch(exam_type, chapter, seen_hashes)
        new_batch = [q for q in new_batch if q["hash"] not in seen_hashes]
        if not q_data and new_batch:
            q_data = new_batch.pop(0)
        question_pool.extend(new_batch)
    if not q_data:
        conn.close()
        raise HTTPException(503, "Could not generate a unique question. Please start a new session.")
    seen_hashes.add(q_data["hash"])
    conn.execute(
        "INSERT INTO dynamic_attempts (session_id, user_id, question_text, options, correct_answer, explanation, source) VALUES (?,?,?,?,?,?,?)",
        (session_id, u["uid"], q_data["question"], json.dumps(q_data["options"]),
         q_data["correct_answer"], q_data["explanation"], q_data.get("source", "ai")))
    conn.commit()
    attempt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE dynamic_sessions SET seen_hashes=?, question_pool=? WHERE id=?",
        (json.dumps(list(seen_hashes)), json.dumps(question_pool), session_id))
    conn.commit()
    score = session["score"] or 0
    total = session["total"] or 0
    conn.close()
    return {"attempt_id": attempt_id, "question": q_data["question"],
            "options": q_data["options"], "difficulty": q_data.get("difficulty", "hard"),
            "running_score": score, "running_total": total,
            "running_pct": round(score / max(total, 1) * 100, 1) if total else 0}

@app.post("/api/session/dynamic/answer")
def dynamic_answer(req: DynamicAnswerReq, u=Depends(get_current_user)):
    conn    = get_db()
    attempt = conn.execute(
        "SELECT * FROM dynamic_attempts WHERE id=? AND user_id=?",
        (req.attempt_id, u["uid"])).fetchone()
    if not attempt:
        conn.close()
        raise HTTPException(404, "Attempt not found")
    attempt = dict(attempt)
    if attempt["user_answer"] is not None:
        conn.close()
        return {"message": "Already answered", "is_correct": bool(attempt["is_correct"]),
                "correct_answer": attempt["correct_answer"], "explanation": attempt["explanation"]}
    session = conn.execute(
        "SELECT * FROM dynamic_sessions WHERE id=? AND user_id=?",
        (attempt["session_id"], u["uid"])).fetchone()
    if not session or not session["is_active"]:
        conn.close()
        raise HTTPException(400, "Session is not active")
    is_correct = 1 if req.user_answer == attempt["correct_answer"] else 0
    conn.execute(
        "UPDATE dynamic_attempts SET user_answer=?, is_correct=?, time_taken=? WHERE id=?",
        (req.user_answer, is_correct, req.time_taken, req.attempt_id))
    new_score = (session["score"] or 0) + is_correct
    new_total = (session["total"] or 0) + 1
    conn.execute("UPDATE dynamic_sessions SET score=?, total=? WHERE id=?",
                 (new_score, new_total, attempt["session_id"]))
    conn.commit()
    if is_correct:
        award_coins(conn, u["uid"], COINS_PER_CORRECT)
    consec = update_consecutive_correct(conn, u["uid"], bool(is_correct))
    xp_gain = 15 if is_correct else 3
    award_xp(conn, u["uid"], xp_gain, "dynamic_question")
    if req.time_taken > 0:
        conn.execute(
            "INSERT INTO question_latency (user_id,question_id,session_type,time_taken_sec,is_correct,chapter,difficulty) VALUES (?,?,?,?,?,?,?)",
            (u["uid"], None, "dynamic", req.time_taken, is_correct,
             session["chapter"], "hard"))
        conn.commit()

    # Real-time latency alert
    lat_alert = None
    if req.time_taken > LATENCY_SLOW_SINGLE:
        lat_alert = {
            "type": "slow_single",
            "message": f"This question took {req.time_taken}s. In exams, aim for under {LATENCY_SLOW_SINGLE}s.",
            "tip": "If stuck, eliminate 2 options first, then decide."
        }

    # FIX: Build coaching trigger when answer is wrong in dynamic mode
    coaching_trigger = None
    if not is_correct:
        opts = json.loads(attempt["options"]) if isinstance(attempt["options"], str) else attempt["options"]
        coaching_trigger = {
            "should_coach": True,
            "question_id": None,
            "question_text": attempt["question_text"],
            "options": opts,
            "correct_answer": attempt["correct_answer"],
            "user_answer": req.user_answer,
            "explanation": attempt["explanation"],
            "chapter": session["chapter"] or "",
            "message": "You got this wrong — your AI coach can help you understand it now!",
        }

    conn.close()
    return {"is_correct": bool(is_correct), "correct_answer": attempt["correct_answer"],
            "explanation": attempt["explanation"], "xp_earned": xp_gain,
            "coins_earned": COINS_PER_CORRECT if is_correct else 0,
            "running_score": new_score, "running_total": new_total,
            "running_pct": round(new_score / max(new_total, 1) * 100, 1),
            "consecutive_correct": consec,
            "latency_alert": lat_alert,
            # FIX: coaching_trigger lets frontend auto-open teach-me for wrong answers
            "coaching_trigger": coaching_trigger}

@app.post("/api/session/dynamic/stop")
def dynamic_stop(req: DynamicStopReq, u=Depends(get_current_user)):
    conn    = get_db()
    session = conn.execute(
        "SELECT * FROM dynamic_sessions WHERE id=? AND user_id=?",
        (req.session_id, u["uid"])).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Session not found")
    session = dict(session)
    if not session["is_active"]:
        attempts = conn.execute(
            "SELECT * FROM dynamic_attempts WHERE session_id=? ORDER BY id",
            (req.session_id,)).fetchall()
        conn.close()
        return _dynamic_summary(session, attempts, [])
    conn.execute(
        "UPDATE dynamic_sessions SET is_active=0, ended_at=?, question_pool='[]' WHERE id=?",
        (datetime.now().isoformat(), req.session_id))
    conn.commit()
    attempts = conn.execute(
        "SELECT * FROM dynamic_attempts WHERE session_id=? ORDER BY id",
        (req.session_id,)).fetchall()
    score = session["score"] or 0
    total = session["total"] or 0
    bonus_xp = min(total * 2, 100)
    award_xp(conn, u["uid"], bonus_xp, "dynamic_session_end")
    new_achs = check_and_award_achievements(conn, u["uid"], {
        "score": score, "total": total, "mode": "dynamic", "dyn_total": total})
    lat_stats  = compute_latency_stats(conn, u["uid"], 30)
    lat_alerts = get_latency_alerts(lat_stats)
    conn.close()
    return _dynamic_summary(session, attempts, new_achs, bonus_xp, lat_alerts)

def _dynamic_summary(session, attempts, new_achs, bonus_xp=0, lat_alerts=None):
    score = session["score"] or 0
    total = session["total"] or 0
    pct   = round(score / max(total, 1) * 100, 1) if total else 0
    results = []
    for a in attempts:
        a = dict(a)
        if a["user_answer"] is None:
            continue
        results.append({"attempt_id": a["id"], "question": a["question_text"],
                        "options": json.loads(a["options"]) if isinstance(a["options"], str) else a["options"],
                        "correct_answer": a["correct_answer"],
                        "user_answer": a["user_answer"], "is_correct": bool(a["is_correct"]),
                        "explanation": a["explanation"], "time_taken": a["time_taken"]})
    return {"session_id": session["id"], "chapter": session["chapter"],
            "score": score, "total": total, "percentage": pct, "bonus_xp": bonus_xp,
            "results": results, "new_achievements": new_achs,
            "latency_alerts": (lat_alerts or [])[:2],
            "performance": ("excellent" if pct >= 85 else "good" if pct >= 65 else "needs_practice")}

@app.get("/api/session/dynamic/{session_id}/status")
def dynamic_status(session_id: int, u=Depends(get_current_user)):
    conn    = get_db()
    session = conn.execute(
        "SELECT * FROM dynamic_sessions WHERE id=? AND user_id=?",
        (session_id, u["uid"])).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Session not found")
    attempts = conn.execute(
        "SELECT id, is_correct, user_answer, time_taken FROM dynamic_attempts WHERE session_id=? ORDER BY id",
        (session_id,)).fetchall()
    conn.close()
    score = session["score"] or 0
    total = session["total"] or 0
    pool  = json.loads(session["question_pool"] or "[]")
    return {"session_id": session_id, "is_active": bool(session["is_active"]),
            "chapter": session["chapter"], "score": score, "total": total,
            "percentage": round(score / max(total, 1) * 100, 1) if total else 0,
            "questions_seen": len(json.loads(session["seen_hashes"] or "[]")),
            "pool_remaining": len(pool), "attempts": [dict(a) for a in attempts]}

@app.get("/api/session/dynamic/history")
def dynamic_history(u=Depends(get_current_user)):
    conn  = get_db()
    rows  = conn.execute(
        "SELECT id, chapter, exam_type, score, total, is_active, created_at, ended_at "
        "FROM dynamic_sessions WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (u["uid"],)).fetchall()
    conn.close()
    return {"sessions": [
        {"session_id": r["id"], "chapter": r["chapter"], "exam_type": r["exam_type"],
         "score": r["score"] or 0, "total": r["total"] or 0,
         "percentage": round((r["score"] or 0) / max(r["total"] or 1, 1) * 100, 1),
         "is_active": bool(r["is_active"]), "created_at": r["created_at"], "ended_at": r["ended_at"]}
        for r in rows
    ]}

# ── AI COACH ──────────────────────────────────────────────────────────────────

def _get_coach_memory(conn, user_id: int) -> str:
    row = conn.execute("SELECT summary FROM coach_memory WHERE user_id=?", (user_id,)).fetchone()
    return row["summary"] if row else ""

def _compress_coach_memory(conn, user_id: int, name: str, exam_type: str):
    messages = conn.execute(
        "SELECT msg_type, message FROM coach_messages WHERE user_id=? ORDER BY id DESC LIMIT 60",
        (user_id,)).fetchall()
    if len(messages) < 5:
        return
    convo_text = "\n".join(
        f"[{m['msg_type'].upper()}]: {m['message'][:200]}" for m in reversed(messages))
    prompt = f"""Summarize this AI coach conversation for {name} ({exam_type} student).
Extract key info in 150 words: 1.Topics struggled with 2.Confidence level 3.Learning style 4.Weak areas 5.Goals 6.Progress 7.Decision speed issues
Conversation:\n{convo_text}\nWrite a brief coach briefing. Plain text only."""
    try:
        summary = groq_chat(prompt, temperature=0.4, json_mode=False)
        conn.execute(
            "INSERT OR REPLACE INTO coach_memory (user_id, summary, updated_at) VALUES (?,?,?)",
            (user_id, summary, datetime.now().isoformat()))
        conn.commit()
    except Exception as exc:
        logger.error("Memory compression failed: %s", exc)

def _build_coach_system_prompt(u: dict, conn, memory: str, question_ctx: Optional[dict]) -> str:
    uid = u["uid"]
    total_att  = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    total_cor  = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=? AND is_correct=1", (uid,)).fetchone()[0]
    acc        = round(total_cor / max(total_att, 1) * 100, 1) if total_att else 0
    recent_tests = conn.execute(
        "SELECT score, total FROM daily_tests WHERE user_id=? AND completed=1 ORDER BY id DESC LIMIT 5",
        (uid,)).fetchall()
    recent_scores = [round(t["score"] / max(t["total"], 1) * 100, 1) for t in recent_tests]
    weak_chapters = conn.execute("""
        SELECT q.chapter, SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) as wc
        FROM test_attempts ta JOIN questions q ON ta.question_id=q.id
        WHERE ta.user_id=? GROUP BY q.chapter ORDER BY wc DESC LIMIT 3
    """, (uid,)).fetchall()
    weak_list = ", ".join(r["chapter"] for r in weak_chapters) if weak_chapters else "None yet"
    streak   = get_streak(conn, uid)
    consec   = conn.execute("SELECT consecutive_correct FROM users WHERE id=?", (uid,)).fetchone()
    consec_n = consec["consecutive_correct"] if consec else 0
    lat_stats = compute_latency_stats(conn, uid, 30)
    lat_note = ""
    if lat_stats["total_tracked"] >= 5:
        lat_note = f"\nDecision speed: avg {lat_stats['avg_time']}s/question (trend: {lat_stats['trend']})"
    memory_block = f"\n\nWhat I remember about you:\n{memory}" if memory else ""
    q_block = ""
    if question_ctx:
        opts = question_ctx.get("options", [])
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except Exception:
                opts = []
        ua   = question_ctx.get("user_answer")
        ca   = question_ctx.get("correct_answer")
        user_chose  = opts[ua] if ua is not None and isinstance(ua, int) and 0 <= ua < len(opts) else "No answer"
        correct_opt = opts[ca] if ca is not None and isinstance(ca, int) and 0 <= ca < len(opts) else "Unknown"
        q_block = f"""
THE QUESTION THEY GOT WRONG:
Question: {question_ctx.get('question', '')}
They chose: "{user_chose}" ❌
Correct answer: "{correct_opt}" ✓
Explanation: {question_ctx.get('explanation', '')}"""
    return f"""You are ExamAI Coach — a friendly, supportive exam coach.

STUDENT: {u.get('name','Student')} | Exam: {u.get('exam_type','General')}
Questions done: {total_att} | Accuracy: {acc}% | Streak: {streak} days | On a roll: {consec_n} correct in a row
Recent test scores: {recent_scores or 'No tests yet'} | Weak areas: {weak_list}{lat_note}{memory_block}{q_block}

YOUR COACHING STYLE:
- Talk like a friendly teacher. Keep responses SHORT: 3-5 sentences max.
- Always use actual student data — never give generic advice.
- Use emojis occasionally. If discouraged, remind them of something they did well.
- If their decision speed is slow, proactively mention a quick timing technique.

IMPORTANT: Be warm and human. Students should feel supported, not judged."""

@app.post("/api/coach/chat")
def coach_chat(req: CoachChatReq, u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    memory = _get_coach_memory(conn, uid)
    system_prompt = _build_coach_system_prompt(u, conn, memory, req.question_context)
    recent = conn.execute(
        "SELECT msg_type, message FROM coach_messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, COACH_MEMORY_LIMIT)).fetchall()
    recent = list(reversed(recent))
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        role = "user" if m["msg_type"] == "user" else "assistant"
        messages.append({"role": role, "content": m["message"]})
    messages.append({"role": "user", "content": req.message})
    response_text = groq_chat_with_history(messages, temperature=0.75)
    q_ctx_str = json.dumps(req.question_context) if req.question_context else None
    conn.execute(
        "INSERT INTO coach_messages (user_id, message, msg_type, question_context) VALUES (?,?,'user',?)",
        (uid, req.message, q_ctx_str))
    conn.execute(
        "INSERT INTO coach_messages (user_id, message, msg_type) VALUES (?,?,'coach')",
        (uid, response_text))
    conn.commit()
    msg_count = conn.execute("SELECT COUNT(*) FROM coach_messages WHERE user_id=?", (uid,)).fetchone()[0]
    if msg_count > 0 and msg_count % 40 == 0:
        _compress_coach_memory(conn, uid, u.get("name", "Student"), u.get("exam_type", "exam"))
    new_achs = check_and_award_achievements(conn, uid, {"mode": "coach"})
    conn.close()
    return {"response": response_text, "new_achievements": new_achs}

@app.get("/api/coach/history")
def coach_history(limit: int = Query(20, ge=1, le=100), u=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, msg_type, message, question_context, created_at FROM coach_messages "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (u["uid"], limit)).fetchall()
    conn.close()
    return {"messages": [
        {"id": r["id"], "type": r["msg_type"], "message": r["message"],
         "question_context": json.loads(r["question_context"]) if r["question_context"] else None,
         "created_at": r["created_at"]}
        for r in reversed(rows)
    ]}

@app.get("/api/coach/daily-insight")
def daily_insight(u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    total_att = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    total_cor = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=? AND is_correct=1", (uid,)).fetchone()[0]
    acc       = round(total_cor / max(total_att, 1) * 100, 1) if total_att else 0
    weak_q    = conn.execute("""
        SELECT q.chapter FROM test_attempts ta JOIN questions q ON ta.question_id=q.id
        WHERE ta.user_id=? AND ta.is_correct=0 GROUP BY q.chapter ORDER BY COUNT(*) DESC LIMIT 1
    """, (uid,)).fetchone()
    weakest   = weak_q["chapter"] if weak_q else "your exam topics"
    streak    = get_streak(conn, uid)
    memory    = _get_coach_memory(conn, uid)
    consec    = conn.execute("SELECT consecutive_correct FROM users WHERE id=?", (uid,)).fetchone()
    consec_n  = consec["consecutive_correct"] if consec else 0
    lat_stats = compute_latency_stats(conn, uid, 30)
    lat_note  = f"\nDecision speed: {lat_stats['avg_time']}s avg (trend: {lat_stats['trend']})" if lat_stats["total_tracked"] >= 5 else ""
    memory_hint = f"\nContext: {memory[:200]}" if memory else ""
    conn.close()
    prompt = f"""Write a short motivational message for {u.get('name','Student')} preparing for {u.get('exam_type','their exam')}.
Stats: {total_att} questions done, {acc}% accuracy, {streak}-day streak, {consec_n} correct in a row. Weakest: {weakest}.{lat_note}{memory_hint}
Write 2-3 sentences. Personal and electric. Reference actual numbers. No labels or JSON."""
    insight = groq_chat(prompt, temperature=0.9, json_mode=False)
    return {"insight": insight, "type": "daily_psych"}

# ── TEACH ME ──────────────────────────────────────────────────────────────────

@app.post("/api/coach/teach-me")
def teach_me_start(req: TeachMeReq, u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    if req.question_id:
        q = conn.execute("SELECT * FROM questions WHERE id=? AND user_id=?",
                         (req.question_id, uid)).fetchone()
        if not q:
            conn.close()
            raise HTTPException(404, "Question not found")
        q = dict(q)
        question_text  = q["question"]
        options        = json.loads(q["options"])
        correct_answer = q["correct_answer"]
        user_answer    = req.user_answer
        explanation    = q["explanation"]
        chapter        = q["chapter"]
    else:
        question_text  = req.question_text or ""
        options        = req.options or []
        correct_answer = req.correct_answer or 0
        user_answer    = req.user_answer
        explanation    = req.explanation or ""
        chapter        = req.chapter or ""
    if not question_text:
        conn.close()
        raise HTTPException(400, "Question text is required")
    conn.execute(
        "INSERT INTO teach_sessions (user_id, question_id, question_text, correct_answer, user_answer, options, explanation, chapter) VALUES (?,?,?,?,?,?,?,?)",
        (uid, req.question_id, question_text, correct_answer, user_answer,
         json.dumps(options), explanation, chapter))
    conn.commit()
    session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    opts        = options
    ua          = user_answer
    ca          = correct_answer
    user_chose  = opts[ua]  if ua is not None and isinstance(ua, int) and 0 <= ua < len(opts) else "Did not answer"
    correct_opt = opts[ca]  if ca is not None and isinstance(ca, int) and 0 <= ca < len(opts) else (opts[0] if opts else "")
    exam_type = u.get("exam_type", "General")
    memory    = _get_coach_memory(conn, uid)
    system_prompt = f"""You are a friendly {exam_type} tutor.
THE QUESTION: {question_text}
OPTIONS: {opts}
THEY CHOSE: "{user_chose}" ❌  
CORRECT ANSWER: "{correct_opt}" ✓
STANDARD EXPLANATION: {explanation}
{f'What I know: {memory[:150]}' if memory else ''}
HOW TO TEACH: 1.Empathy sentence 2.Explain why "{correct_opt}" is correct with analogy 3.Explain trap of "{user_chose}" 4.Memory trick 5.Brief follow-up question
TONE: Warm, simple, encouraging. Under 200 words."""
    opening = groq_chat_with_history(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": "Please explain why I got this wrong."}],
        temperature=0.7)
    q_ctx = json.dumps({"question": question_text, "options": opts,
                         "correct_answer": ca, "user_answer": ua, "explanation": explanation})
    conn.execute("INSERT INTO coach_messages (user_id, message, msg_type, question_context) VALUES (?,?,'user',?)",
                 (uid, "Teach me this question I got wrong.", q_ctx))
    conn.execute("INSERT INTO coach_messages (user_id, message, msg_type) VALUES (?,?,'coach')",
                 (uid, opening))
    conn.commit()
    conn.close()
    return {"session_id": session_id, "opening": opening, "question": question_text,
            "options": opts, "correct_answer": ca, "user_answer": ua, "chapter": chapter}

@app.post("/api/coach/teach-me/reply")
def teach_me_reply(req: TeachMeReplyReq, u=Depends(get_current_user)):
    conn    = get_db()
    session = conn.execute(
        "SELECT * FROM teach_sessions WHERE id=? AND user_id=?",
        (req.session_id, u["uid"])).fetchone()
    if not session:
        conn.close()
        raise HTTPException(404, "Teach session not found")
    session = dict(session)
    exam_type = u.get("exam_type", "General")
    memory    = _get_coach_memory(conn, u["uid"])
    opts      = json.loads(session["options"]) if isinstance(session["options"], str) else session["options"]
    ca        = session["correct_answer"]
    correct_opt = opts[ca] if ca is not None and isinstance(ca, int) and 0 <= ca < len(opts) else ""
    system_prompt = f"""You are a friendly {exam_type} tutor.
QUESTION: {session['question_text']}
CORRECT ANSWER: "{correct_opt}"
If confused, simplify and use a different analogy. If understood, give a similar practice question.
Under 150 words. Encouraging. {f'Student context: {memory[:100]}' if memory else ''}"""
    recent = conn.execute(
        "SELECT msg_type, message FROM coach_messages WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (u["uid"],)).fetchall()
    recent = list(reversed(recent))
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        role = "user" if m["msg_type"] == "user" else "assistant"
        messages.append({"role": role, "content": m["message"]})
    messages.append({"role": "user", "content": req.message})
    response = groq_chat_with_history(messages, temperature=0.7)
    conn.execute("INSERT INTO coach_messages (user_id, message, msg_type) VALUES (?,?,'user')", (u["uid"], req.message))
    conn.execute("INSERT INTO coach_messages (user_id, message, msg_type) VALUES (?,?,'coach')", (u["uid"], response))
    if any(w in req.message.lower() for w in ["understand", "got it", "makes sense", "thanks", "clear now", "i see"]):
        conn.execute("UPDATE teach_sessions SET completed=1 WHERE id=?", (req.session_id,))
        award_xp(conn, u["uid"], 30, "teach_me_complete")
    conn.commit()
    new_achs = check_and_award_achievements(conn, u["uid"], {"mode": "teach"})
    conn.close()
    return {"response": response, "new_achievements": new_achs}

@app.get("/api/coach/teach-me/failed-questions")
def get_failed_questions(limit: int = Query(10, ge=1, le=30), u=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("""
        SELECT q.id, q.chapter, q.sub_chapter, q.question, q.options,
               q.correct_answer, q.explanation, q.difficulty,
               SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) AS wrong_count,
               SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) AS right_count,
               MAX(ta.user_answer) AS last_user_answer
        FROM questions q JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
        WHERE q.user_id=? GROUP BY q.id HAVING wrong_count > 0
        ORDER BY wrong_count DESC, right_count ASC LIMIT ?
    """, (u["uid"], u["uid"], limit)).fetchall()
    conn.close()
    return {"questions": [
        {"id": r["id"], "chapter": r["chapter"], "sub_chapter": r["sub_chapter"],
         "question": r["question"], "options": json.loads(r["options"]),
         "correct_answer": r["correct_answer"], "explanation": r["explanation"],
         "difficulty": r["difficulty"], "wrong_count": r["wrong_count"],
         "right_count": r["right_count"] or 0, "last_user_answer": r["last_user_answer"]}
        for r in rows
    ]}

# ── DAILY CHALLENGE ───────────────────────────────────────────────────────────

@app.get("/api/challenge/daily")
def get_daily_challenge(u=Depends(get_current_user)):
    today = date.today().isoformat()
    conn  = get_db()
    existing = conn.execute(
        "SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?",
        (u["uid"], today)).fetchone()
    if existing:
        existing = dict(existing)
        conn.close()
        return {"challenge_date": today, "question": existing["question_text"],
                "options": json.loads(existing["options"]), "chapter": existing.get("chapter"),
                "completed": bool(existing["completed"]), "user_answer": existing.get("user_answer"),
                "correct_answer": existing["correct_answer"] if existing["completed"] else None,
                "explanation": existing["explanation"] if existing["completed"] else None,
                "xp_reward": existing.get("xp_reward", DAILY_CHALLENGE_XP)}
    exam_row  = conn.execute("SELECT exam_type, chapters FROM users WHERE id=?", (u["uid"],)).fetchone()
    exam_type = exam_row["exam_type"] if exam_row else "General"
    chapters  = json.loads(exam_row["chapters"]) if exam_row and exam_row["chapters"] else []
    chapter = random.choice(chapters) if chapters else exam_type
    prompt = f"""Generate 1 VERY HARD challenge question about "{chapter}" for {exam_type}.
OUTPUT FORMAT:
{{
  "question": "Full question text",
  "options": ["Option A", "Option B", "Option C", "Option D"],
  "correct_answer": 2,
  "explanation": "Detailed explanation"
}}
RULES: correct_answer must be INTEGER 0-3. Return ONLY valid JSON."""
    try:
        raw  = groq_chat(prompt, temperature=0.8)
        data = parse_json(raw)
        vq   = validate_question_dict(data)
        if not vq:
            conn.close()
            raise HTTPException(503, "Could not generate challenge question")
        conn.execute(
            "INSERT INTO daily_challenges (user_id, challenge_date, question_text, options, correct_answer, explanation, chapter) VALUES (?,?,?,?,?,?,?)",
            (u["uid"], today, vq["question"], json.dumps(vq["options"]),
             vq["correct_answer"], vq["explanation"], chapter))
        conn.commit()
        conn.close()
        return {"challenge_date": today, "question": vq["question"], "options": vq["options"],
                "chapter": chapter, "completed": False, "user_answer": None,
                "correct_answer": None, "explanation": None, "xp_reward": DAILY_CHALLENGE_XP}
    except Exception as exc:
        conn.close()
        logger.error("Daily challenge generation error: %s", exc)
        raise HTTPException(503, "Failed to generate daily challenge")

@app.post("/api/challenge/daily/submit")
def submit_daily_challenge(req: DailyChallengeSubmitReq, u=Depends(get_current_user)):
    today = date.today().isoformat()
    conn  = get_db()
    challenge = conn.execute(
        "SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?",
        (u["uid"], today)).fetchone()
    if not challenge:
        conn.close()
        raise HTTPException(404, "No challenge found for today.")
    challenge = dict(challenge)
    if challenge["completed"]:
        conn.close()
        return {"already_submitted": True,
                "is_correct": req.user_answer == challenge["correct_answer"],
                "correct_answer": challenge["correct_answer"],
                "explanation": challenge["explanation"]}
    is_correct = req.user_answer == challenge["correct_answer"]
    xp_reward  = challenge.get("xp_reward", DAILY_CHALLENGE_XP) if is_correct else 10
    conn.execute(
        "UPDATE daily_challenges SET user_answer=?, completed=1 WHERE user_id=? AND challenge_date=?",
        (req.user_answer, u["uid"], today))
    conn.commit()
    award_xp(conn, u["uid"], xp_reward, "daily_challenge")
    if is_correct:
        award_coins(conn, u["uid"], 10)
    update_consecutive_correct(conn, u["uid"], is_correct)
    new_achs = check_and_award_achievements(conn, u["uid"], {
        "score": 1 if is_correct else 0, "total": 1, "mode": "challenge"
    })
    conn.close()
    return {"is_correct": is_correct, "correct_answer": challenge["correct_answer"],
            "explanation": challenge["explanation"], "xp_earned": xp_reward,
            "coins_earned": 10 if is_correct else 0, "new_achievements": new_achs}

# ── BOOKMARKS ─────────────────────────────────────────────────────────────────

@app.post("/api/questions/bookmark")
def bookmark_question(req: BookmarkReq, u=Depends(get_current_user)):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM bookmarks WHERE user_id=? AND question_id=?",
        (u["uid"], req.question_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM bookmarks WHERE user_id=? AND question_id=?",
                     (u["uid"], req.question_id))
        conn.commit(); conn.close()
        return {"bookmarked": False, "message": "Bookmark removed"}
    q = conn.execute("SELECT id FROM questions WHERE id=? AND user_id=?",
                     (req.question_id, u["uid"])).fetchone()
    if not q:
        conn.close()
        raise HTTPException(404, "Question not found")
    conn.execute("INSERT INTO bookmarks (user_id, question_id, note) VALUES (?,?,?)",
                 (u["uid"], req.question_id, req.note))
    conn.commit()
    new_achs = check_and_award_achievements(conn, u["uid"], {"mode": "bookmark"})
    conn.close()
    return {"bookmarked": True, "message": "Question bookmarked!", "new_achievements": new_achs}

@app.get("/api/questions/bookmarks")
def get_bookmarks(u=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("""
        SELECT b.id as bookmark_id, b.note, b.created_at as bookmarked_at,
               q.id, q.chapter, q.sub_chapter, q.question, q.options,
               q.correct_answer, q.explanation, q.difficulty
        FROM bookmarks b JOIN questions q ON b.question_id = q.id
        WHERE b.user_id=? ORDER BY b.created_at DESC
    """, (u["uid"],)).fetchall()
    conn.close()
    return {"bookmarks": [
        {"bookmark_id": r["bookmark_id"], "note": r["note"], "bookmarked_at": r["bookmarked_at"],
         "question": {"id": r["id"], "chapter": r["chapter"], "sub_chapter": r["sub_chapter"],
                      "question": r["question"], "options": json.loads(r["options"]),
                      "correct_answer": r["correct_answer"], "explanation": r["explanation"],
                      "difficulty": r["difficulty"]}}
        for r in rows
    ]}

# ── POWER-UPS / HINTS ─────────────────────────────────────────────────────────

@app.post("/api/powerup/hint")
def use_hint(req: UseHintReq, u=Depends(get_current_user)):
    conn = get_db()
    q = conn.execute("SELECT * FROM questions WHERE id=? AND user_id=?",
                     (req.question_id, u["uid"])).fetchone()
    if not q:
        conn.close()
        raise HTTPException(404, "Question not found")
    q = dict(q)
    cost = HINT_COST_COINS if req.hint_type == "hint" else FIFTY_FIFTY_COST_COINS
    coins_row = conn.execute("SELECT coins FROM users WHERE id=?", (u["uid"],)).fetchone()
    current_coins = coins_row["coins"] if coins_row else 0
    if current_coins < cost:
        conn.close()
        raise HTTPException(402, f"Not enough coins. You have {current_coins}, need {cost}.")
    options = json.loads(q["options"])
    result  = {}
    if req.hint_type == "fifty_fifty":
        correct_idx  = q["correct_answer"]
        wrong_indices = [i for i in range(4) if i != correct_idx]
        random.shuffle(wrong_indices)
        result = {"type": "fifty_fifty", "eliminate": wrong_indices[:2],
                  "message": f"Eliminated 2 wrong options! Used {cost} coins."}
    else:
        prompt = f"""Give a SHORT hint for this question WITHOUT revealing the answer.
Question: {q['question']}
Options: {options}
Correct answer is option {q['correct_answer'] + 1} ("{options[q['correct_answer']]}")
Write 1-2 sentences guiding toward the right answer without mentioning it directly."""
        hint_text = groq_chat(prompt, temperature=0.5, json_mode=False)
        result = {"type": "hint", "hint": hint_text,
                  "message": f"Hint unlocked! Used {cost} coins."}
    spend_coins(conn, u["uid"], cost)
    conn.execute(
        "INSERT INTO powerup_uses (user_id, powerup_type, question_id, result) VALUES (?,?,?,?)",
        (u["uid"], req.hint_type, req.question_id, json.dumps(result)))
    conn.commit()
    new_coins = conn.execute("SELECT coins FROM users WHERE id=?", (u["uid"],)).fetchone()["coins"]
    conn.close()
    return {**result, "coins_remaining": new_coins, "coins_spent": cost}

@app.get("/api/user/coins")
def get_coins(u=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT coins FROM users WHERE id=?", (u["uid"],)).fetchone()
    conn.close()
    return {"coins": row["coins"] if row else 0,
            "hint_cost": HINT_COST_COINS,
            "fifty_fifty_cost": FIFTY_FIFTY_COST_COINS}

# ── QUESTION REPORT ───────────────────────────────────────────────────────────

@app.post("/api/questions/report")
def report_question(req: ReportQuestionReq, u=Depends(get_current_user)):
    valid_reasons = {"wrong_answer", "bad_options", "unclear", "irrelevant", "duplicate"}
    if req.reason not in valid_reasons:
        raise HTTPException(400, f"Reason must be one of: {valid_reasons}")
    conn = get_db()
    q = conn.execute("SELECT id FROM questions WHERE id=? AND user_id=?",
                     (req.question_id, u["uid"])).fetchone()
    if not q:
        conn.close()
        raise HTTPException(404, "Question not found")
    conn.execute("INSERT INTO question_reports (user_id, question_id, reason) VALUES (?,?,?)",
                 (u["uid"], req.question_id, req.reason))
    conn.execute("UPDATE questions SET report_count = report_count + 1 WHERE id=?", (req.question_id,))
    conn.commit(); conn.close()
    return {"success": True, "message": "Report submitted. Thank you!"}

# ── STUDY PLAN ────────────────────────────────────────────────────────────────

@app.post("/api/study-plan/generate")
def generate_study_plan(req: StudyPlanReq, u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    chapters_row = conn.execute("SELECT chapters FROM users WHERE id=?", (uid,)).fetchone()
    chapters     = json.loads(chapters_row["chapters"]) if chapters_row and chapters_row["chapters"] else []
    ch_data = []
    for ch in chapters:
        att, cor, acc = chapter_stats(conn, uid, ch)
        subs_p, subs_t = get_sub_chapter_progress(conn, uid, ch)
        ch_data.append({"chapter": ch, "attempted": att, "accuracy": acc,
                         "subs_practiced": subs_p, "subs_total": subs_t})
    total_att = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    lat_stats = compute_latency_stats(conn, uid, 30)
    conn.close()
    exam_type = u.get("exam_type", "General")
    days_left = ""
    if req.exam_date:
        try:
            exam_dt = date.fromisoformat(req.exam_date)
            days_left = f"\nExam date: {req.exam_date} ({(exam_dt - date.today()).days} days remaining)"
        except Exception:
            pass
    lat_note = ""
    if lat_stats["total_tracked"] >= 5:
        lat_note = f"\nDecision speed: avg {lat_stats['avg_time']}s/question — include timed drills."
    prompt = f"""Create a personalized study plan for {u.get('name','Student')} preparing for {exam_type}.{days_left}
Daily available hours: {req.daily_hours}. Total questions practiced: {total_att}.{lat_note}
Chapter performance: {json.dumps(ch_data, indent=2)}
Create a WEEK-BY-WEEK study plan with: 1.Priority chapters (weakest first) 2.Daily tasks 3.Weekly milestones 4.Timed practice drills 5.Test schedule 6.Tips.
Format with ### headers for each week. 500-700 words."""
    plan = groq_chat(prompt, temperature=0.6, json_mode=False)
    conn2 = get_db()
    conn2.execute("INSERT OR REPLACE INTO study_plans (user_id, plan, updated_at) VALUES (?,?,?)",
                  (uid, plan, datetime.now().isoformat()))
    conn2.commit(); conn2.close()
    return {"plan": plan, "exam_type": exam_type, "generated_at": datetime.now().isoformat()}

@app.get("/api/study-plan")
def get_study_plan(u=Depends(get_current_user)):
    conn = get_db()
    row  = conn.execute("SELECT plan, updated_at FROM study_plans WHERE user_id=?", (u["uid"],)).fetchone()
    conn.close()
    if not row:
        return {"plan": None, "message": "No study plan yet. Generate one!"}
    return {"plan": row["plan"], "updated_at": row["updated_at"]}

# ── MOOD CHECKIN ──────────────────────────────────────────────────────────────

@app.post("/api/mood/checkin")
def mood_checkin(req: MoodCheckinReq, u=Depends(get_current_user)):
    conn = get_db()
    conn.execute("INSERT INTO mood_checkins (user_id,mood,energy,note) VALUES (?,?,?,?)",
                 (u["uid"], req.mood, req.energy, req.note))
    conn.commit()
    mood_map     = {1: "very low", 2: "low", 3: "neutral", 4: "good", 5: "excellent"}
    mood_label   = mood_map.get(req.mood, "neutral")
    energy_label = mood_map.get(req.energy, "neutral")
    memory       = _get_coach_memory(conn, u["uid"])
    prompt = f"""Student feels {mood_label} mood and {energy_label} energy before studying for {u.get('exam_type','their exam')}.
{f'Note: {req.note}' if req.note else ''}
{f'What I know: {memory[:150]}' if memory else ''}
Give SHORT, caring response (2-3 sentences):
- mood ≤ 2: empathy + 1 tiny easy task
- mood 3: small encouraging challenge
- mood ≥ 4: celebrate + stretch goal
End with 1 concrete action."""
    response = groq_chat(prompt, temperature=0.8, json_mode=False)
    new_achs = check_and_award_achievements(conn, u["uid"], {"mode": "mood"})
    conn.close()
    return {"response": response, "mood": req.mood, "energy": req.energy, "new_achievements": new_achs}

@app.get("/api/mood/history")
def mood_history(u=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT mood, energy, note, checkin_date FROM mood_checkins WHERE user_id=? ORDER BY id DESC LIMIT 14",
        (u["uid"],)).fetchall()
    conn.close()
    return {"history": [dict(r) for r in rows]}

# ── AI EXPLAIN ────────────────────────────────────────────────────────────────

@app.post("/api/explain")
def explain(req: ExplainReq, u=Depends(get_current_user)):
    conn = get_db()
    q    = conn.execute("SELECT * FROM questions WHERE id=? AND user_id=?",
                        (req.question_id, u["uid"])).fetchone()
    conn.close()
    if not q:
        raise HTTPException(404, "Question not found")
    q = dict(q)
    opts        = json.loads(q["options"])
    user_opt    = opts[req.user_answer] if 0 <= req.user_answer < len(opts) else "Unknown"
    correct_opt = opts[q["correct_answer"]]
    exam_type   = u.get("exam_type", "General")
    prompt = f"""A student answered this {exam_type} question incorrectly. Explain simply.
Question: {q['question']}
All options: {opts}
Student chose: "{user_opt}"
Correct answer: "{correct_opt}"
Write with exactly these 4 sections:
**Why "{correct_opt}" is correct:** [2-3 sentences with example]
**Why your answer was wrong:** [1-2 sentences, kind]
**Memory Tip:** [One simple trick]
**Quick Practice:** Q: [Similar question] A: [Answer with reason]
Simple and encouraging."""
    raw = groq_chat(prompt, temperature=0.6, json_mode=False)
    return {"explanation": raw, "question": q["question"],
            "correct_answer": q["correct_answer"], "correct_option": correct_opt}

# ── ANALYTICS ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics")
def analytics(u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    total_att  = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    total_cor  = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=? AND is_correct=1", (uid,)).fetchone()[0]
    tests_done = conn.execute("SELECT COUNT(*) FROM daily_tests WHERE user_id=? AND completed=1", (uid,)).fetchone()[0]
    weak_done  = conn.execute("SELECT COUNT(*) FROM weak_sessions WHERE user_id=? AND completed=1", (uid,)).fetchone()[0]
    dyn_sessions = conn.execute("SELECT COUNT(*) FROM dynamic_sessions WHERE user_id=? AND is_active=0", (uid,)).fetchone()[0]
    dyn_row = conn.execute(
        "SELECT COALESCE(SUM(score),0) as ds, COALESCE(SUM(total),0) as dt "
        "FROM dynamic_sessions WHERE user_id=? AND is_active=0", (uid,)).fetchone()
    dyn_score   = dyn_row["ds"] or 0
    dyn_total_q = dyn_row["dt"] or 0
    dyn_acc     = round(dyn_score / max(dyn_total_q, 1) * 100, 1) if dyn_total_q else 0
    dyn_history = conn.execute(
        "SELECT id, chapter, score, total, created_at, ended_at FROM dynamic_sessions "
        "WHERE user_id=? AND is_active=0 ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    dyn_best = conn.execute(
        "SELECT score, total FROM dynamic_sessions WHERE user_id=? AND is_active=0 AND total>0 "
        "ORDER BY CAST(score AS FLOAT)/total DESC LIMIT 1", (uid,)).fetchone()
    dyn_best_pct = round((dyn_best["score"] or 0) / max(dyn_best["total"] or 1, 1) * 100, 1) if dyn_best else 0
    dyn_longest  = conn.execute(
        "SELECT MAX(total) FROM dynamic_sessions WHERE user_id=? AND is_active=0", (uid,)).fetchone()[0] or 0
    streak = get_streak(conn, uid)
    chapters_row = conn.execute("SELECT chapters FROM users WHERE id=?", (uid,)).fetchone()
    chapters     = json.loads(chapters_row["chapters"]) if chapters_row and chapters_row["chapters"] else []
    ch_perf = []
    for i, ch in enumerate(chapters):
        att, cor, acc  = chapter_stats(conn, uid, ch)
        unlocked       = is_unlocked(conn, uid, chapters, i)
        complete       = is_chapter_complete(conn, uid, chapters, i)
        total_qs       = conn.execute("SELECT COUNT(*) FROM questions WHERE user_id=? AND chapter=?",
                                      (uid, ch)).fetchone()[0]
        subs_p, subs_t = get_sub_chapter_progress(conn, uid, ch)
        ch_perf.append({
            "chapter": ch, "index": i, "total_questions": total_qs,
            "attempted": att, "correct": cor, "accuracy": acc,
            "unlocked": unlocked, "complete": complete,
            "subs_practiced": subs_p, "subs_total": subs_t,
            "status": ("complete" if complete else "weak" if acc < 50 and att > 0 else
                       "moderate" if acc < 75 and att > 0 else "strong" if att > 0 else "untouched"),
        })
    daily_hist = conn.execute("""
        SELECT test_date, score, total, chapter_name,
               CASE WHEN total > 0 THEN ROUND(CAST(score AS FLOAT)/total*100,1) ELSE 0.0 END AS percentage
        FROM daily_tests WHERE user_id=? AND completed=1 ORDER BY test_date DESC LIMIT 14
    """, (uid,)).fetchall()
    weak_qs = conn.execute("""
        SELECT q.id, q.chapter, q.sub_chapter, q.question,
               SUM(CASE WHEN ta.is_correct=0 THEN 1 ELSE 0 END) AS wrong_count,
               SUM(CASE WHEN ta.is_correct=1 THEN 1 ELSE 0 END) AS right_count
        FROM questions q JOIN test_attempts ta ON ta.question_id=q.id AND ta.user_id=?
        WHERE q.user_id=? GROUP BY q.id HAVING wrong_count > right_count
        ORDER BY wrong_count DESC LIMIT 10
    """, (uid, uid)).fetchall()
    user_row = conn.execute("SELECT xp, level, achievements, consecutive_correct, coins FROM users WHERE id=?", (uid,)).fetchone()
    xp = user_row["xp"] if user_row else 0
    level = user_row["level"] if user_row else 1
    achievements = json.loads(user_row["achievements"] or "[]") if user_row else []
    consec = user_row["consecutive_correct"] if user_row else 0
    coins = user_row["coins"] if user_row else 0
    completions = conn.execute(
        "SELECT chapter, completed_at FROM chapter_completions WHERE user_id=? ORDER BY completed_at",
        (uid,)).fetchall()
    time_dist = conn.execute("""
        SELECT strftime('%H', attempted_at) as hour, COUNT(*) as cnt, SUM(is_correct) as correct
        FROM test_attempts WHERE user_id=? GROUP BY hour ORDER BY hour
    """, (uid,)).fetchall()
    diff_stats = conn.execute("""
        SELECT q.difficulty, COUNT(ta.id) as total_att, SUM(ta.is_correct) as correct_att
        FROM test_attempts ta JOIN questions q ON ta.question_id=q.id
        WHERE ta.user_id=? GROUP BY q.difficulty
    """, (uid,)).fetchall()
    teach_stats = conn.execute(
        "SELECT COUNT(*) as total, SUM(completed) as done FROM teach_sessions WHERE user_id=?",
        (uid,)).fetchone()
    coach_msgs = conn.execute("SELECT COUNT(*) FROM coach_messages WHERE user_id=?", (uid,)).fetchone()[0]
    bookmarks  = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=?", (uid,)).fetchone()[0]
    challenges_done = conn.execute(
        "SELECT COUNT(*) FROM daily_challenges WHERE user_id=? AND completed=1", (uid,)).fetchone()[0]
    global_participated = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND submitted_at IS NOT NULL", (uid,)).fetchone()[0]
    best_global_rank = conn.execute(
        "SELECT MIN(rank) FROM global_participants WHERE user_id=? AND rank IS NOT NULL", (uid,)).fetchone()[0]
    global_wins = conn.execute(
        "SELECT COUNT(*) FROM global_participants WHERE user_id=? AND rank=1", (uid,)).fetchone()[0]

    lat_stats  = compute_latency_stats(conn, uid, 30)
    lat_alerts = get_latency_alerts(lat_stats)

    strong_chapters = [c for c in ch_perf if c["status"] in ("strong", "complete")]
    weak_chapters   = [c for c in ch_perf if c["status"] == "weak"]
    conn.close()

    xp_for_next = XP_LEVELS[min(level, len(XP_LEVELS)-1)] if level < len(XP_LEVELS) else None
    xp_prev     = XP_LEVELS[max(0, level-1)]
    xp_progress = round((xp - xp_prev) / max(1, (xp_for_next or xp+1) - xp_prev) * 100, 1) if xp_for_next else 100

    return JSONResponse(content={
        "overview": {
            "total_attempted":      total_att,
            "total_correct":        total_cor,
            "overall_accuracy":     round(total_cor / max(total_att, 1) * 100, 1) if total_att else 0,
            "tests_completed":      tests_done,
            "weak_sessions_done":   weak_done,
            "streak":               streak,
            "consecutive_correct":  consec,
            "chapters_covered":     sum(1 for c in ch_perf if c["attempted"] > 0),
            "chapters_total":       len(chapters),
            "chapters_complete":    sum(1 for c in ch_perf if c["complete"]),
            "xp": xp, "level": level, "xp_progress": xp_progress, "xp_for_next": xp_for_next,
            "coins": coins, "coach_messages": coach_msgs,
            "teach_sessions":       teach_stats["total"] if teach_stats else 0,
            "teach_sessions_done":  teach_stats["done"] if teach_stats else 0,
            "bookmarks":            bookmarks, "challenges_done": challenges_done,
            "global_tests_done":    global_participated,
            "best_global_rank":     best_global_rank,
            "global_wins":          global_wins,
        },
        "chapter_performance":  ch_perf,
        "chapter_completions":  [{"chapter": r["chapter"], "completed_at": r["completed_at"]}
                                  for r in completions],
        "daily_history":        [{"test_date": r["test_date"], "score": r["score"] or 0,
                                   "total": r["total"] or 0, "percentage": r["percentage"] or 0.0,
                                   "chapter_name": r["chapter_name"]}
                                  for r in daily_hist],
        "weak_areas":           [{"id": r["id"], "chapter": r["chapter"], "sub_chapter": r["sub_chapter"],
                                   "question": r["question"], "wrong_count": r["wrong_count"],
                                   "right_count": r["right_count"]} for r in weak_qs],
        "achievements":         achievements,
        "strong_chapters":      [c["chapter"] for c in strong_chapters[:3]],
        "weak_chapters":        [c["chapter"] for c in weak_chapters[:3]],
        "time_distribution":    [{"hour": int(r["hour"]), "count": r["cnt"], "correct": r["correct"] or 0}
                                  for r in time_dist],
        "difficulty_stats":     [{"difficulty": r["difficulty"], "total": r["total_att"],
                                   "correct": r["correct_att"] or 0} for r in diff_stats],
        "dynamic_stats": {
            "sessions_completed":  dyn_sessions,
            "total_questions":     dyn_total_q,
            "total_correct":       dyn_score,
            "overall_accuracy":    dyn_acc,
            "best_session_pct":    dyn_best_pct,
            "longest_session_q":   dyn_longest,
            "history": [
                {"session_id": r["id"], "chapter": r["chapter"], "score": r["score"] or 0,
                 "total": r["total"] or 0,
                 "percentage": round((r["score"] or 0) / max(r["total"] or 1, 1) * 100, 1),
                 "created_at": r["created_at"], "ended_at": r["ended_at"]}
                for r in dyn_history
            ],
        },
        "latency": {
            "stats":  lat_stats,
            "alerts": lat_alerts,
        },
    }, headers={"Cache-Control": "no-store"})

# ── PERSONAL BESTS ────────────────────────────────────────────────────────────

@app.get("/api/user/personal-bests")
def personal_bests(u=Depends(get_current_user)):
    conn = get_db()
    uid  = u["uid"]
    best_daily = conn.execute("""
        SELECT test_date, score, total,
               ROUND(CAST(score AS FLOAT)/MAX(total,1)*100, 1) as pct
        FROM daily_tests WHERE user_id=? AND completed=1 ORDER BY pct DESC LIMIT 1
    """, (uid,)).fetchone()
    best_streak = 0
    dates = conn.execute(
        "SELECT test_date FROM daily_tests WHERE user_id=? AND completed=1 ORDER BY test_date", (uid,)).fetchall()
    if dates:
        current_streak = max_streak = 1
        for i in range(1, len(dates)):
            try:
                d1 = date.fromisoformat(dates[i-1]["test_date"])
                d2 = date.fromisoformat(dates[i]["test_date"])
                if (d2 - d1).days == 1:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 1
            except Exception:
                current_streak = 1
        best_streak = max_streak
    best_dynamic = conn.execute("""
        SELECT chapter, score, total,
               ROUND(CAST(score AS FLOAT)/MAX(total,1)*100, 1) as pct, created_at
        FROM dynamic_sessions WHERE user_id=? AND is_active=0 AND total > 0
        ORDER BY pct DESC LIMIT 1
    """, (uid,)).fetchone()
    total_att = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=?", (uid,)).fetchone()[0]
    total_cor = conn.execute("SELECT COUNT(*) FROM test_attempts WHERE user_id=? AND is_correct=1", (uid,)).fetchone()[0]
    best_global = conn.execute(
        "SELECT MIN(rank) as best_rank, MAX(score) as best_score FROM global_participants "
        "WHERE user_id=? AND submitted_at IS NOT NULL", (uid,)).fetchone()
    lat_stats = compute_latency_stats(conn, uid, 90)
    conn.close()
    return {
        "best_daily_test": dict(best_daily) if best_daily else None,
        "best_streak_days": best_streak,
        "best_dynamic_session": dict(best_dynamic) if best_dynamic else None,
        "best_global_rank": best_global["best_rank"] if best_global else None,
        "best_global_score": best_global["best_score"] if best_global else None,
        "total_questions_answered": total_att,
        "overall_accuracy": round(total_cor / max(total_att, 1) * 100, 1) if total_att else 0,
        "best_avg_speed_sec": lat_stats.get("avg_time", 0),
        "fastest_question_sec": min(lat_stats.get("times_percentile_90", 999), 999),
    }

# ── STATIC FILES ──────────────────────────────────────────────────────────────

import os as _os
_frontend = _os.path.join(_os.path.dirname(__file__), "..", "frontend")
if _os.path.exists(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="static")
