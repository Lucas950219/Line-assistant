import argparse
import asyncio
import base64
from collections import OrderedDict
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request as OutgoingRequest, urlopen

from dotenv import dotenv_values
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
import uvicorn

from agenda import Agenda
from codex_client import ask_codex, CodexError, find_codex


ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("uvicorn.error")
MAX_WEBHOOK_BYTES = 256 * 1024
MAX_INPUT_CHARACTERS = 3000
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def send_reply(access_token: str, reply_token: str, answer: str) -> None:
    # LINE counts text limits in UTF-16 code units.
    text = answer.encode("utf-16-le")[:9000].decode("utf-16-le", errors="ignore")
    request = OutgoingRequest(
        REPLY_URL,
        data=json.dumps({"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}).encode("utf-8"),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8):
            LOGGER.info("已透過 LINE Messaging API 回覆訊息。")
    except HTTPError as exc:
        LOGGER.warning("LINE 回覆失敗，HTTP %d", exc.code)
    except (URLError, OSError):
        LOGGER.warning("無法連線到 LINE 回覆 API。")


def create_app(
    secret: str,
    access_token: str,
    owner_id: str,
    executable: str,
    model: str | None,
    *,
    agenda_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    agenda = Agenda(agenda_path or (ROOT / ".runtime" / "agenda.sqlite3"))
    running = asyncio.Lock()
    seen_events: OrderedDict[str, float] = OrderedDict()

    async def process_message(event_id: str, reply_token: str, text: str) -> None:
        if len(text) > MAX_INPUT_CHARACTERS:
            answer = f"請把訊息縮短為 {MAX_INPUT_CHARACTERS} 字以內後再傳送。"
        elif running.locked():
            answer = "我正在回覆上一則訊息，請稍後再傳一次。"
        else:
            async with running:
                try:
                    answer = await asyncio.to_thread(agenda.reply, executable, model, owner_id, event_id, text)
                except CodexError as exc:
                    LOGGER.warning("%s", exc)
                    answer = "AI 暫時無法回覆，請稍後再傳一次。"
                except (sqlite3.Error, OSError) as exc:
                    LOGGER.error("讀寫行程資料庫失敗（%s）。", type(exc).__name__)
                    answer = "行程資料暫時無法讀取或儲存，請稍後再試。"
        if answer:
            await asyncio.to_thread(send_reply, access_token, reply_token, answer)

    async def process_batch(messages: list[tuple[str, str, str]]) -> None:
        await asyncio.gather(*(process_message(event_id, token, text) for event_id, token, text in messages))

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks):
        signature = request.headers.get("x-line-signature", "").encode("utf-8")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing LINE signature")

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_WEBHOOK_BYTES:
                raise HTTPException(status_code=413, detail="Webhook too large")
            body.extend(chunk)

        expected = base64.b64encode(hmac.digest(secret.encode("utf-8"), body, "sha256"))
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid LINE signature")

        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise HTTPException(status_code=400, detail="Missing webhook events")

        now = monotonic()
        while seen_events and next(iter(seen_events.values())) <= now - 600:
            seen_events.popitem(last=False)

        messages = []
        for event in payload["events"]:
            if not isinstance(event, dict) or event.get("type") != "message" or event.get("mode") != "active":
                continue
            source, message = event.get("source"), event.get("message")
            if not isinstance(source, dict) or source.get("type") != "user" or source.get("userId") != owner_id:
                continue
            if not isinstance(message, dict) or message.get("type") != "text":
                continue
            text, token, event_id = message.get("text"), event.get("replyToken"), event.get("webhookEventId")
            if not all(isinstance(value, str) and value.strip() for value in (text, token, event_id)):
                continue
            if event_id in seen_events:
                continue
            seen_events[event_id] = now
            messages.append((event_id, token, text))

        if messages:
            background_tasks.add_task(process_batch, messages)
        return Response(status_code=200)

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="透過本機 Codex 回覆 LINE 訊息")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("ask", help="在本機測試 Codex 回覆")
    smoke.add_argument("message")
    serve = commands.add_parser("serve", help="啟動 LINE webhook")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = {**dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False), **os.environ}
    try:
        executable = find_codex(settings.get("CODEX_BIN"))
        model = settings.get("CODEX_MODEL") or None
        if args.command == "ask":
            print(ask_codex(executable, model, args.message))
            return 0

        secret = settings.get("LINE_CHANNEL_SECRET") or ""
        access_token = settings.get("LINE_CHANNEL_ACCESS_TOKEN") or ""
        owner_id = settings.get("LINE_OWNER_USER_ID") or ""
        if not secret or not access_token or not re.fullmatch(r"U[0-9a-f]{32}", owner_id):
            parser.error("請先依 README 填寫 .env 的 LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN 和 LINE_OWNER_USER_ID。")
        uvicorn.run(create_app(secret, access_token, owner_id, executable, model), host="127.0.0.1", port=args.port)
    except CodexError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
