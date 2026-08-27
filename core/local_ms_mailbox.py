"""Local Microsoft mailbox pool provider.

The importer accepts Xinlan/BH Mailer "common" account rows.  Microsoft
accounts with Client Id + refresh token are read through Microsoft Graph;
rows without OAuth material fall back to IMAP only when inbound server fields
are present and usable.
"""

from __future__ import annotations

import csv
import email as email_lib
import hashlib
import imaplib
import json
import re
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

import requests

from core.base_mailbox import BaseMailbox, MailboxAccount, _extract_verification_link


GRAPH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
DEFAULT_GRAPH_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"
DEFAULT_IMAP_OAUTH_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
DEFAULT_IMAP_HOST = "outlook.office365.com"
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / ".local_ms_mailbox_pool_state.json"


@dataclass(frozen=True)
class LocalMicrosoftMailboxEntry:
    email: str
    password: str = ""
    login_account: str = ""
    imap_host: str = ""
    imap_port: str = ""
    imap_account_type: str = ""
    imap_security: str = ""
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_security: str = ""
    note: str = ""
    proxy_mode: str = ""
    proxy: str = ""
    label: str = ""
    recovery_email: str = ""
    recovery_password: str = ""
    client_id: str = ""
    refresh_token: str = ""
    totp_secret: str = ""
    raw: str = ""

    @property
    def key(self) -> str:
        return self.email.strip().lower()

    @property
    def graph_ready(self) -> bool:
        return bool(self.client_id and self.refresh_token)

    @property
    def imap_ready(self) -> bool:
        return bool(self.imap_host and (self.login_account or self.email) and self.password)

    def credentials(self) -> dict:
        return {
            "email": self.email,
            "password": self.password,
            "login_account": self.login_account,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_account_type": self.imap_account_type,
            "imap_security": self.imap_security,
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "recovery_email": self.recovery_email,
            "recovery_password": self.recovery_password,
            "totp_secret": self.totp_secret,
        }


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _safe_text(value: object) -> str:
    return str(value or "").strip().strip("\ufeff")


def _csv_split(line: str, delimiter: str) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=delimiter, quotechar='"', skipinitialspace=True))
    except Exception:
        return line.split(delimiter)


def split_xinlan_common_line(line: str) -> list[str]:
    text = str(line or "").strip().strip("\ufeff")
    if not text:
        return []
    if "----" in text:
        return [item.strip() for item in text.split("----")]
    if "\t" in text:
        return [item.strip() for item in text.split("\t")]
    if "，" in text:
        return [item.strip() for item in _csv_split(text, "，")]
    if "," in text:
        return [item.strip() for item in _csv_split(text, ",")]
    return [item.strip() for item in re.split(r"\s+", text) if item.strip()]


_CLIENT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def expand_short_oauth_row(parts: list[str]) -> list[str] | None:
    """识别市面常见的短格式，补齐成 19 段心蓝格式。

    这类号通常是 ``email----password----client_id----refresh_token``，但
    client_id 与 refresh_token 的先后并不统一，密码段也可能整个缺失。只在
    parts[1:] 里真的出现 UUID 形态的 client_id 时才启用，避免把正常的心蓝
    短行（第 3 段是登录名）误判。识别不出返回 None，交回原有的按位解析。
    """
    if not 3 <= len(parts) <= 8:
        return None
    tail = list(enumerate(parts))[1:]
    client_idx = next((i for i, value in tail if _CLIENT_ID_RE.match(value or "")), -1)
    if client_idx < 0:
        return None
    refresh = ""
    for i, value in tail:
        if i == client_idx or not value:
            continue
        if _CLIENT_ID_RE.match(value):
            continue
        if len(value) >= 20:
            refresh = value
            break
    row = [""] * 19
    row[0] = parts[0]
    # client_id 落在第 2 段说明这批号没带密码
    row[1] = parts[1] if client_idx != 1 else ""
    row[16] = parts[client_idx]
    row[17] = refresh
    return row


def parse_xinlan_common_rows(text: str) -> list[LocalMicrosoftMailboxEntry]:
    entries: list[LocalMicrosoftMailboxEntry] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//") or line.startswith("'"):
            continue
        parts = split_xinlan_common_line(line)
        if not parts:
            continue
        padded = expand_short_oauth_row(parts) or parts + [""] * max(0, 19 - len(parts))
        email = _safe_text(padded[0])
        if "@" not in email:
            continue
        entry = LocalMicrosoftMailboxEntry(
            email=email,
            password=_safe_text(padded[1]),
            login_account=_safe_text(padded[2]) or email,
            imap_host=_safe_text(padded[3]),
            imap_port=_safe_text(padded[4]),
            imap_account_type=_safe_text(padded[5]),
            imap_security=_safe_text(padded[6]),
            smtp_host=_safe_text(padded[7]),
            smtp_port=_safe_text(padded[8]),
            smtp_security=_safe_text(padded[9]),
            note=_safe_text(padded[10]),
            proxy_mode=_safe_text(padded[11]),
            proxy=_safe_text(padded[12]),
            label=_safe_text(padded[13]),
            recovery_email=_safe_text(padded[14]),
            recovery_password=_safe_text(padded[15]),
            client_id=_safe_text(padded[16]),
            refresh_token=_safe_text(padded[17]),
            totp_secret=_safe_text(padded[18]),
            raw=line,
        )
        if entry.key in seen:
            continue
        seen.add(entry.key)
        entries.append(entry)
    return entries


class LocalMicrosoftMailboxPool(BaseMailbox):
    """Use existing Outlook/Hotmail/Live accounts from a local text pool."""

    _lock = threading.Lock()

    def __init__(
        self,
        *,
        pool_text: str = "",
        pool_file: str = "",
        state_file: str = "",
        graph_scope: str = "",
        imap_scope: str = "",
        allow_reuse: bool = False,
        proxy: str = None,
    ):
        self.pool_text = str(pool_text or "")
        self.pool_file = str(pool_file or "").strip()
        self.state_file = Path(state_file or DEFAULT_STATE_FILE)
        self.graph_scope = str(graph_scope or DEFAULT_GRAPH_SCOPE).strip()
        self.imap_scope = str(imap_scope or DEFAULT_IMAP_OAUTH_SCOPE).strip()
        self.allow_reuse = bool(allow_reuse)
        self.proxy = {"http": proxy, "https": proxy} if proxy else None
        # 某个邮箱的 refresh_token 若没有 Mail.Read 授权，记下来，之后直接走 IMAP
        self._prefer_imap_oauth: dict[str, bool] = {}

    @classmethod
    def from_config(cls, config: dict) -> "LocalMicrosoftMailboxPool":
        return cls(
            pool_text=config.get("local_ms_pool_text", ""),
            pool_file=config.get("local_ms_pool_file", ""),
            state_file=config.get("local_ms_pool_state_file", ""),
            graph_scope=config.get("local_ms_graph_scope", ""),
            imap_scope=config.get("local_ms_imap_scope", ""),
            allow_reuse=_truthy(config.get("local_ms_pool_allow_reuse")),
            proxy=config.get("proxy") or None,
        )

    def _load_pool_text(self) -> str:
        chunks: list[str] = []
        if self.pool_text.strip():
            chunks.append(self.pool_text)
        if self.pool_file:
            path = Path(self.pool_file).expanduser()
            if not path.exists():
                raise RuntimeError(f"本地微软邮箱池文件不存在: {path}")
            chunks.append(path.read_text(encoding="utf-8-sig"))
        combined = "\n".join(chunks)
        if not combined.strip():
            raise RuntimeError("本地微软邮箱池为空，请粘贴心蓝通用格式或配置文件路径")
        return combined

    def _entries(self) -> list[LocalMicrosoftMailboxEntry]:
        entries = parse_xinlan_common_rows(self._load_pool_text())
        if not entries:
            raise RuntimeError("本地微软邮箱池未解析到有效邮箱")
        return entries

    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {"used": {}}

    def _save_state(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _source_id(self) -> str:
        material = f"{self.pool_file}\n{self.pool_text}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:16]

    def _reserve(self, entry: LocalMicrosoftMailboxEntry) -> None:
        if self.allow_reuse:
            return
        state = self._state()
        used = dict(state.get("used") or {})
        used[entry.key] = {
            "email": entry.email,
            "reserved_at": datetime.now(timezone.utc).isoformat(),
            "source_id": self._source_id(),
        }
        state["used"] = used
        self._save_state(state)

    def _available_entry(self) -> LocalMicrosoftMailboxEntry:
        entries = self._entries()
        state = self._state()
        used = set((state.get("used") or {}).keys())
        for entry in entries:
            if self.allow_reuse or entry.key not in used:
                return entry
        raise RuntimeError(f"本地微软邮箱池已用尽: total={len(entries)}")

    def peek_email(self) -> str:
        return self._available_entry().email

    def get_email(self) -> MailboxAccount:
        with self._lock:
            entry = self._available_entry()
            self._reserve(entry)

        credentials = entry.credentials()
        credentials = {key: value for key, value in credentials.items() if value}
        return MailboxAccount(
            email=entry.email,
            account_id=entry.key,
            extra={
                "provider_account": {
                    "provider_type": "mailbox",
                    "provider_name": "local_ms_pool",
                    "login_identifier": entry.login_account or entry.email,
                    "display_name": entry.email,
                    "credentials": credentials,
                    "metadata": {
                        "source": "xinlan_common",
                        "has_graph_refresh_token": bool(entry.graph_ready),
                        "has_imap_config": bool(entry.imap_ready),
                    },
                },
                "provider_resource": {
                    "provider_type": "mailbox",
                    "provider_name": "local_ms_pool",
                    "resource_type": "mailbox",
                    "resource_identifier": entry.key,
                    "handle": entry.email,
                    "display_name": entry.email,
                    "metadata": {
                        "email": entry.email,
                        "source": "xinlan_common",
                        "reserved": not self.allow_reuse,
                    },
                },
            },
        )

    def _entry_for_account(self, account: MailboxAccount) -> LocalMicrosoftMailboxEntry:
        account_email = str(getattr(account, "email", "") or "").strip().lower()
        extra = dict(getattr(account, "extra", {}) or {})
        provider_account = dict(extra.get("provider_account") or {})
        credentials = dict(provider_account.get("credentials") or {})
        if credentials:
            return LocalMicrosoftMailboxEntry(
                email=str(credentials.get("email") or account.email or ""),
                password=str(credentials.get("password") or ""),
                login_account=str(credentials.get("login_account") or account.email or ""),
                imap_host=str(credentials.get("imap_host") or ""),
                imap_port=str(credentials.get("imap_port") or ""),
                imap_account_type=str(credentials.get("imap_account_type") or ""),
                imap_security=str(credentials.get("imap_security") or ""),
                client_id=str(credentials.get("client_id") or ""),
                refresh_token=str(credentials.get("refresh_token") or ""),
                recovery_email=str(credentials.get("recovery_email") or ""),
                recovery_password=str(credentials.get("recovery_password") or ""),
                totp_secret=str(credentials.get("totp_secret") or ""),
            )

        for entry in self._entries():
            if entry.key == account_email:
                return entry
        raise RuntimeError(f"本地微软邮箱池未找到账号: {getattr(account, 'email', '')}")

    @staticmethod
    def _decode_mime(value: str) -> str:
        parts = []
        for raw, charset in decode_header(value or ""):
            if isinstance(raw, bytes):
                parts.append(raw.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(str(raw or ""))
        return "".join(parts)

    @staticmethod
    def _message_id(mail: dict) -> str:
        return str(mail.get("id") or mail.get("internetMessageId") or mail.get("receivedDateTime") or "")

    @staticmethod
    def _message_text(mail: dict) -> str:
        body = mail.get("body") or {}
        return " ".join(
            str(value or "")
            for value in (
                mail.get("subject"),
                mail.get("bodyPreview"),
                body.get("content") if isinstance(body, dict) else "",
            )
        )

    def _access_token(self, entry: LocalMicrosoftMailboxEntry, scope: str) -> str:
        if not entry.graph_ready:
            raise RuntimeError(f"微软邮箱缺少 Client Id 或刷新令牌: {entry.email}")
        response = requests.post(
            GRAPH_TOKEN_URL,
            data={
                "client_id": entry.client_id,
                "grant_type": "refresh_token",
                "refresh_token": entry.refresh_token,
                "scope": scope,
            },
            proxies=self.proxy,
            timeout=25,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Microsoft refresh_token 换 access_token 失败(scope={scope}): "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        token = str((response.json() or {}).get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Microsoft refresh_token 响应缺少 access_token")
        return token

    def _graph_access_token(self, entry: LocalMicrosoftMailboxEntry) -> str:
        return self._access_token(entry, self.graph_scope)

    def _graph_messages(self, entry: LocalMicrosoftMailboxEntry) -> list[dict]:
        token = self._graph_access_token(entry)
        response = requests.get(
            GRAPH_MESSAGES_URL,
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            params={
                "$top": "25",
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,bodyPreview,receivedDateTime,from,toRecipients,body",
            },
            proxies=self.proxy,
            timeout=25,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Microsoft Graph 读取邮件失败: HTTP {response.status_code} {response.text[:200]}")
        payload = response.json() or {}
        return list(payload.get("value") or [])

    def _imap_connect(self, entry: LocalMicrosoftMailboxEntry, host: str = ""):
        host = (host or entry.imap_host).strip()
        port = int(entry.imap_port or 993)
        security = entry.imap_security.lower()
        if port == 993 or "ssl" in security:
            return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
        conn = imaplib.IMAP4(host, port)
        if "tls" in security:
            conn.starttls(ssl_context=ssl.create_default_context())
        return conn

    def _imap_oauth_messages(self, entry: LocalMicrosoftMailboxEntry) -> list[dict]:
        """个人 Outlook 已关闭 IMAP 基础认证，用 refresh_token 换 IMAP token 做 XOAUTH2。"""
        token = self._access_token(entry, self.imap_scope)
        conn = self._imap_connect(entry, host=entry.imap_host or DEFAULT_IMAP_HOST)
        try:
            user = entry.login_account or entry.email
            auth_string = f"user={user}\x01auth=Bearer {token}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth_string.encode())
            return self._imap_collect(conn)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _imap_messages(self, entry: LocalMicrosoftMailboxEntry) -> list[dict]:
        if not entry.imap_ready:
            raise RuntimeError(f"微软邮箱没有可用的 Graph token，也没有 IMAP 收件配置: {entry.email}")
        conn = self._imap_connect(entry)
        try:
            conn.login(entry.login_account or entry.email, entry.password)
            return self._imap_collect(conn)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _imap_collect(self, conn) -> list[dict]:
        messages: list[dict] = []
        conn.select("INBOX", readonly=True)
        _, msg_nums = conn.search(None, "ALL")
        ids = msg_nums[0].split() if msg_nums and msg_nums[0] else []
        for mid in reversed(ids[-30:]):
            _, data = conn.fetch(mid, "(RFC822)")
            if not data or not data[0]:
                continue
            msg = email_lib.message_from_bytes(data[0][1])
            subject = self._decode_mime(str(msg.get("Subject", "") or ""))
            parts: list[str] = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() not in ("text/plain", "text/html"):
                        continue
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="replace"))
            messages.append({
                "id": str(msg.get("Message-ID") or mid.decode("ascii", errors="ignore")),
                "subject": subject,
                "bodyPreview": " ".join(parts),
            })
        return messages

    def _messages(self, account: MailboxAccount) -> list[dict]:
        entry = self._entry_for_account(account)
        errors: list[str] = []
        if entry.graph_ready:
            # 市面上多数 Outlook 号的 refresh_token 只授予 IMAP/POP/SMTP，没有
            # Mail.Read，Graph 会 401；失败一次后记住，之后直接走 IMAP XOAUTH2。
            if not self._prefer_imap_oauth.get(entry.key):
                try:
                    return self._graph_messages(entry)
                except Exception as exc:
                    errors.append(f"Graph: {exc}")
                    self._prefer_imap_oauth[entry.key] = True
            try:
                return self._imap_oauth_messages(entry)
            except Exception as exc:
                errors.append(f"IMAP XOAUTH2: {exc}")
        if entry.imap_ready:
            try:
                return self._imap_messages(entry)
            except Exception as exc:
                errors.append(f"IMAP 基础认证: {exc}")
        if not errors:
            errors.append("既没有 Client Id + 刷新令牌，也没有 IMAP 收件配置")
        raise RuntimeError(f"微软邮箱 {entry.email} 收件失败 -> " + " | ".join(errors))

    def get_current_ids(self, account: MailboxAccount) -> set:
        try:
            return {self._message_id(mail) for mail in self._messages(account) if self._message_id(mail)}
        except Exception:
            return set()

    @staticmethod
    def _clean_search_text(text: str) -> str:
        cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", cleaned, flags=re.I | re.S)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", " ", cleaned)
        return cleaned

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
    ) -> str:
        seen = set(before_ids or [])
        pattern = re.compile(code_pattern or r"(?<!#)(?<!\d)(\d{6})(?!\d)")
        start = time.time()
        while time.time() - start < timeout:
            for mail in self._messages(account):
                mid = self._message_id(mail)
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                text = self._clean_search_text(self._message_text(mail))
                if keyword and keyword.lower() not in text.lower():
                    continue
                match = pattern.search(text)
                if match:
                    return match.group(1) if match.groups() else match.group(0)
            time.sleep(5)
        raise TimeoutError(f"等待验证码超时 ({timeout}s)")

    def wait_for_link(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
    ) -> str:
        seen = set(before_ids or [])
        start = time.time()
        while time.time() - start < timeout:
            for mail in self._messages(account):
                mid = self._message_id(mail)
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                link = _extract_verification_link(self._message_text(mail), keyword)
                if link:
                    return link
            time.sleep(5)
        raise TimeoutError(f"等待验证链接超时 ({timeout}s)")
