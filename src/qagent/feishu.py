"""Small Feishu bridge around the official lark-oapi channel SDK."""

import asyncio
import os
import secrets
import threading
from dataclasses import dataclass
from queue import SimpleQueue


CHANNEL = "feishu"
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str

    @classmethod
    def from_env(cls):
        app_id = os.environ.get("FEISHU_APP_ID", "").strip()
        app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
        if not app_id and not app_secret:
            return None
        if not app_id or not app_secret:
            raise ValueError("飞书配置必须同时提供 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        return cls(app_id, app_secret)


def split_message(text, limit=4000):
    text = str(text)
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        cut = min(limit, len(text))
        if cut < len(text):
            newline = text.rfind("\n", 0, cut)
            if newline > limit // 2:
                cut = newline
        parts.append(text[:cut])
        text = text[cut:]
    return parts


def pairing_code(length=8):
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(length))


class FeishuBridge:
    """Run Feishu's SDK channel on its own asyncio loop and expose events to Textual."""

    def __init__(self, events=None, config=None):
        self.events = events or SimpleQueue()
        self.config = config or FeishuConfig.from_env()
        self.channel = None
        self.loop = None
        self.thread = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self._stop = None

    @property
    def enabled(self):
        return self.config is not None

    def start(self):
        if not self.enabled or self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._thread_main, name="qagent-feishu", daemon=True)
        self.thread.start()

    def _thread_main(self):
        try:
            from lark_oapi.channel import Events, FeishuChannel

            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.channel = FeishuChannel(app_id=self.config.app_id, app_secret=self.config.app_secret,
                                         transport="ws")
            self.channel.on(Events.MESSAGE, self._on_message)
            self.channel.on(Events.ERROR, self._on_error)
            self._stop = asyncio.Event()
            self.loop.create_task(self._serve())
            self.loop.run_forever()
        except Exception as exc:
            self.events.put(("feishu_error", type(exc).__name__))
        finally:
            self.stopped.set()

    async def _serve(self):
        while not self._stop.is_set():
            try:
                await self.channel.connect()
                self.ready.set()
                self.events.put(("feishu_ready", None))
                await self._stop.wait()
            except Exception as exc:
                self.ready.clear()
                self.events.put(("feishu_error", type(exc).__name__))
                if not self._stop.is_set():
                    await asyncio.sleep(5)
            finally:
                self.ready.clear()

    async def _on_message(self, message):
        self.events.put(("feishu_message", {
            "conversation_id": str(getattr(message, "chat_id", "")),
            "conversation_type": str(getattr(message, "chat_type", "unknown")),
            "subject_id": str(getattr(message, "sender_id", "")),
            "subject_name": str(getattr(message, "sender_name", "") or ""),
            "text": str(getattr(message, "content_text", "") or "").strip(),
            "message_id": str(getattr(message, "message_id", "") or ""),
            "mentioned_bot": bool(getattr(message, "mentioned_bot", False)),
        }))

    async def _on_error(self, error):
        self.events.put(("feishu_error", type(error).__name__))

    async def send(self, conversation_id, text, reply_to=None):
        if not self.channel or not self.loop or not self.ready.is_set():
            raise RuntimeError("飞书连接尚未就绪")
        message = {"markdown": str(text)}
        options = {"reply_to": reply_to} if reply_to else None
        future = asyncio.run_coroutine_threadsafe(self.channel.send(conversation_id, message, options), self.loop)
        return await asyncio.wrap_future(future)

    async def _shutdown(self):
        if self._stop:
            self._stop.set()
        if self.channel:
            await self.channel.disconnect()

    async def _stop_loop(self):
        await self._shutdown()
        asyncio.get_running_loop().call_soon(self.loop.stop)

    def stop(self):
        if not self.loop or not self.thread:
            return
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._stop_loop(), self.loop)
            try:
                future.result(timeout=5)
            except Exception:
                self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.ready.clear()
