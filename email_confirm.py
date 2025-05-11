# email_confirm.py
import asyncio, secrets, time, os
from dataclasses import dataclass
from typing import Dict, Literal, Optional
from email.message import EmailMessage
import aiosmtplib

# ─── конфигурация ───────────────────────────────────────────────────
BATCH_INTERVAL  = 10         # частота проверки очереди, сек
RESEND_WINDOW   = 30         # пауза перед разрешённой переотправкой
SMTP_HOST       = "smtp.gmail.com"
SMTP_PORT       = 587
SMTP_USER       = "" # аккаунт
SMTP_PASS       = os.getenv("SMTP_PASS", "") # пароль
BASE_URL        = "https://example.com/confirm?token=" # example.com надо поменять на действительный адрес

# ─── очередь ────────────────────────────────────────────────────────
@dataclass
class Task:
    op: Literal["request", "status", "confirm"]
    email: str
    token: Optional[str] = None
    fut: asyncio.Future | None = None

_QUEUE: 'asyncio.Queue[Task]' = asyncio.Queue()
def get_mail_queue(): return _QUEUE

# ─── продюсер ───────────────────────────────────────────────────────
class MailProducer:
    def __init__(self):
        self._q = _QUEUE
        self._loop = asyncio.get_running_loop()

    async def _call(self, op, **kw):
        fut = self._loop.create_future()
        await self._q.put(Task(op, **kw, fut=fut))
        return await fut

    async def email_confirm(self, email: str):
        """Занести e‑mail в очередь на отправку письма.  
        Если письмо уже было отправлено < RESEND_WINDOW секунд назад,
        метод вернёт False (рано)."""
        return await self._call("request", email=email)

    async def is_confirm(self, email: str):
        """Вернуть dict {sent,timer,confirmed} или None,
        если e‑mail ни разу не запрашивался."""
        return await self._call("status", email=email)

    async def mark_confirmed(self, token: str):
        """Пометить e‑mail подтверждённым по токену из ссылки."""
        return await self._call("confirm", email="", token=token)

# ─── воркер ─────────────────────────────────────────────────────────
class MailWorker:
    def __init__(self):
        self._q = _QUEUE
        self._status: Dict[str, Dict] = {}   # email -> {'sent','ts','confirmed','token'}
        self._token_map: Dict[str, str] = {} # token -> email
        self._running = False

    async def run(self):
        if self._running:
            return
        self._running = True
        asyncio.create_task(self._batch_sender())
        while True:
            t: Task = await self._q.get()
            try:
                if t.op == "request":
                    await self._handle_request(t)
                elif t.op == "status":
                    await self._handle_status(t)
                elif t.op == "confirm":
                    await self._handle_confirm(t)
            finally:
                self._q.task_done()

    # ─── обработчики задач ────────────────────────────────────────
    async def _handle_request(self, t: Task):
        rec = self._status.get(t.email)
        now = time.time()
        if rec:
            if rec["confirmed"]:
                t.fut.set_result(True)        # уже подтверждён
                return
            # если письмо отправлено недавно — рано переотправлять
            if rec["sent"] and now - rec["ts"] < RESEND_WINDOW:
                t.fut.set_result(False)
                return
            # переотправка: убираем старый токен
            self._token_map.pop(rec["token"], None)
        # создаём новую запись/обновляем старую
        token = secrets.token_urlsafe(32)
        self._status[t.email] = {"sent": False, "ts": 0.0,
                                 "confirmed": False, "token": token}
        self._token_map[token] = t.email
        t.fut.set_result(True)

    async def _handle_status(self, t: Task):
        rec = self._status.get(t.email)
        if rec is None:
            t.fut.set_result(None)
            return
        remain = 0
        if rec["sent"]:
            remain = max(0, RESEND_WINDOW - int(time.time() - rec["ts"]))
        ans = {"sent": int(rec["sent"]),
               "timer": remain,
               "confirmed": int(rec["confirmed"])}
        t.fut.set_result(ans)
        if rec["confirmed"]:
            self._cleanup(t.email, rec["token"])

    async def _handle_confirm(self, t: Task):
        email = self._token_map.pop(t.token, None)
        if not email:
            t.fut.set_result(False)
            return
        rec = self._status.get(email)
        if rec:
            rec["confirmed"] = True
            t.fut.set_result(True)
        else:
            t.fut.set_result(False)

    # ─── отправитель пачек ────────────────────────────────────────
    async def _batch_sender(self):
        while True:
            await asyncio.sleep(BATCH_INTERVAL)
            batch = [ (e, r) for e, r in self._status.items()
                      if not r["sent"] and not r["confirmed"] ]
            if not batch:
                continue
            await self._send_batch(batch)
            now = time.time()
            for email, rec in batch:
                rec["sent"] = True
                rec["ts"]   = now

    async def _send_batch(self, batch):
        smtp = aiosmtplib.SMTP(hostname=SMTP_HOST, port=SMTP_PORT, start_tls=True)
        await smtp.connect()
        await smtp.login(SMTP_USER, SMTP_PASS)
        for email, rec in batch:
            msg = self._build_message(email, rec["token"])
            try:
                await smtp.send_message(msg)
            except Exception as e:
                print("SMTP error:", e)
        await smtp.quit()

    def _build_message(self, email: str, token: str) -> EmailMessage:
        html_body = f"""
        <html><body>
        <p>Здравствуйте,</p>
        <p>Если вы выполняли это действие, подтвердите его нажатием на кнопку ниже:</p>
        <a href="{BASE_URL}{token}" style="
           display:inline-block;padding:10px 20px;background-color:#28a745;
           color:white;text-decoration:none;border-radius:5px;">
          Подтвердить
        </a>
        <p>Если это были не вы, просто проигнорируйте это письмо.</p>
        </body></html>"""
        msg = EmailMessage()
        msg["Subject"] = "Подтверждение адреса электронной почты"
        msg["From"] = "No‑Reply <shohruzazzamkulov@gmail.com>"
        msg["To"] = email
        msg.set_content("Чтобы просмотреть письмо, включите HTML‑режим.")
        msg.add_alternative(html_body, subtype="html")
        return msg

    def _cleanup(self, email: str, token: str):
        self._status.pop(email, None)
        self._token_map.pop(token, None)
