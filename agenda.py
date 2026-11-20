from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

from codex_client import CodexError, ask_codex


TAIPEI_TIME = timezone(timedelta(hours=8))
SCHEMA = Path(__file__).resolve().with_name("agenda_schema.json")
QUESTIONS = {
    "title": "要記錄什麼行程？",
    "date": "請問是哪一天？",
    "time": "請問幾點開始？請註明上午或下午。",
    "location": "地點在哪裡？",
    "participants": "會跟誰一起參加？",
}

INTERPRET_MESSAGE = """\
你會收到 JSON，內含台灣目前日期時間、行程草稿及使用者最新的 LINE 訊息。
請依輸出格式解讀最新訊息，answer 欄用繁體中文純文字。不要宣稱已經寫入資料。
- record：使用者要安排或記錄一項行程、補充草稿、或更正草稿。只回傳這則訊息提供的欄位，其他留 null。
  像「下午三點開會」是一項新行程，title 是「開會」，time 是「15:00」，其餘欄位留空。
  date 使用 YYYY-MM-DD，依台灣日期解讀今天、明天或星期幾。使用者沒提日期時，date 留空。
  time 使用 24 小時制 HH:MM。使用者只說下午或「三點」但上午下午不明確時，time 留空。
  location 可包含線上連結；participants 是人名、團隊、公司等對象，有多人時用頓號分隔。
  明確表示地點或對象尚未確定時，把對應欄位設為「未定」。明確表示沒有時，使用「無」。
  有草稿時，從草稿和問句辨認「在會議室」「跟小王」等簡短回答，一次可補充或更正多個欄位。
  單獨回覆「未定」時，如果只有一個欄位待補，視為該欄位的回覆。如果有多個欄位待補，請問使用者是指哪一項。
  有草稿而使用者明確要另建一筆時，用 chat 請對方先完成草稿，或回覆「取消記錄」。
- list：使用者要查已存行程。請把指定日期填入 date。沒有指定日期或想看接下來的行程時 date 留空。
- cancel：使用者想取消這次尚未完成的記錄。只用於取消草稿。
- chat：一般問答、舉例、與新增無關的訊息、含糊答覆，或要求變更已存行程。對一般問題用 answer 回覆。
  目前可以建立和查詢行程、取消草稿。遇到修改、刪除已存行程或開啟提醒的要求，在 answer 說明目前可做的操作。
- 僅依使用者清楚提供的資料填寫。保留人名與地點文字，避免猜測。用 answer 回覆時，維持簡短。
"""


def _valid_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _read_intent(executable: str, model: str | None, now: datetime, draft: dict, message: str) -> dict:
    context = {"now": now.isoformat(timespec="minutes"), "draft": draft or None, "message": message}
    prompt = INTERPRET_MESSAGE + "\n" + json.dumps(context, ensure_ascii=False)
    output = ask_codex(executable, model, prompt, output_schema=SCHEMA)
    try:
        intent = json.loads(output)
    except ValueError as exc:
        raise CodexError("Codex 未回傳可讀取的行程資料。") from exc

    fields = (*QUESTIONS, "answer")
    if (
        not isinstance(intent, dict)
        or intent.get("action") not in ("record", "list", "cancel", "chat")
        or not all(key in intent and (intent[key] is None or isinstance(intent[key], str)) for key in fields)
    ):
        raise CodexError("Codex 回傳的行程資料格式有誤。")

    for key in ("title", "location", "participants"):
        intent[key] = " ".join((intent[key] or "").split())[:200] or None
    if intent["date"] and not _valid_date(intent["date"]):
        raise CodexError("Codex 回傳的行程日期格式有誤。")
    if intent["time"] and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", intent["time"]):
        raise CodexError("Codex 回傳的行程時間格式有誤。")
    return intent


def _details(starts_at: str, title: str, location: str, participants: str) -> str:
    day, hour = starts_at.split("T", 1)
    return f"{day} {hour}　{title}\n地點　{location}\n對象　{participants}"


class Agenda:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agenda_drafts (
                    owner_id TEXT PRIMARY KEY,
                    details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL,
                    participants TEXT NOT NULL,
                    UNIQUE (owner_id, starts_at, title, location, participants)
                );
                CREATE TABLE IF NOT EXISTS agenda_events (
                    event_id TEXT PRIMARY KEY,
                    handled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def reply(self, executable: str, model: str | None, owner_id: str, event_id: str, message: str) -> str | None:
        now = datetime.now(TAIPEI_TIME)
        with closing(sqlite3.connect(self.path)) as db:
            if db.execute("SELECT 1 FROM agenda_events WHERE event_id = ?", (event_id,)).fetchone():
                return None
            current_row = db.execute("SELECT details FROM agenda_drafts WHERE owner_id = ?", (owner_id,)).fetchone()
            draft = json.loads(current_row[0]) if current_row else {}
            if message.strip() in ("取消記錄", "取消紀錄", "取消草稿"):
                intent = {"action": "cancel"}
            else:
                intent = _read_intent(executable, model, now, draft, message)

            if intent["action"] == "chat":
                return (intent["answer"] or "").strip() or "可以告訴我你想記下什麼行程。"
            if intent["action"] == "list":
                return self._list(db, owner_id, now, intent["date"])

            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM agenda_events WHERE handled_at < datetime('now', '-7 days')")
                if not db.execute("INSERT OR IGNORE INTO agenda_events (event_id) VALUES (?)", (event_id,)).rowcount:
                    return None
                latest_row = db.execute("SELECT details FROM agenda_drafts WHERE owner_id = ?", (owner_id,)).fetchone()
                if latest_row != current_row:
                    return "行程草稿剛有更新，請再傳一次這則訊息。"
                if intent["action"] == "cancel":
                    db.execute("DELETE FROM agenda_drafts WHERE owner_id = ?", (owner_id,))
                    return "已取消這次行程記錄。" if draft else "目前沒有待完成的行程記錄。"

                fields = {**draft, **{key: intent[key] for key in QUESTIONS if intent[key]}}
                if not fields.get("date") and fields.get("time"):
                    local_time = time.fromisoformat(fields["time"])
                    if local_time >= now.time().replace(second=0, microsecond=0):
                        fields["date"] = now.date().isoformat()

                questions = [question for key, question in QUESTIONS.items() if not fields.get(key)]
                if questions:
                    db.execute(
                        """INSERT INTO agenda_drafts (owner_id, details) VALUES (?, ?)
                           ON CONFLICT (owner_id) DO UPDATE SET details = excluded.details""",
                        (owner_id, json.dumps(fields, ensure_ascii=False)),
                    )
                    known = " ".join(fields[key] for key in ("date", "time", "title") if fields.get(key))
                    intro = f"我先記下 {known}。" if known else "我可以幫你記錄行程。"
                    return "\n".join([intro, *questions])

                starts_at = f"{fields['date']}T{fields['time']}"
                details = (starts_at, fields["title"], fields["location"], fields["participants"])
                inserted = db.execute(
                    "INSERT OR IGNORE INTO appointments (owner_id, starts_at, title, location, participants) VALUES (?, ?, ?, ?, ?)",
                    (owner_id, *details),
                ).rowcount
                db.execute("DELETE FROM agenda_drafts WHERE owner_id = ?", (owner_id,))
                status = "已記錄行程" if inserted else "這筆行程已經記錄"
                return f"{status}\n{_details(*details)}"

    def _list(self, db: sqlite3.Connection, owner_id: str, now: datetime, day: str | None) -> str:
        if day:
            condition, value = "substr(starts_at, 1, 10) = ?", day
            heading = f"{day} 的行程"
        else:
            condition, value = "starts_at >= ?", now.replace(tzinfo=None).isoformat(timespec="minutes")
            heading = "接下來的行程"
        rows = db.execute(
            f"""SELECT starts_at, title, location, participants FROM appointments
                WHERE owner_id = ? AND {condition} ORDER BY starts_at, id LIMIT 7""",
            (owner_id, value),
        ).fetchall()
        if not rows:
            return f"{heading}目前沒有紀錄。"
        entries = "\n\n".join(_details(*entry) for entry in rows[:6])
        more = "\n\n先顯示前 6 筆。" if len(rows) > 6 else ""
        return f"{heading}\n\n{entries}{more}"
