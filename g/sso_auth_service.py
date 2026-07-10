"""SSO cookie → OIDC token (纯 HTTP Device Flow，无需浏览器)"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests
from dotenv import load_dotenv

_REG_DIR = Path(__file__).resolve().parents[1]
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
AUTH_KEY = f"{OIDC_ISSUER}::{CLIENT_ID}"
SCOPES = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"


class SsoAuthError(RuntimeError):
    pass


def _rfc3339_ns(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000000000Z"


def _decode_jwt_payload(token: str) -> dict:
    try:
        import base64
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def _device_code(proxy: str | None = None) -> dict:
    data = urllib.parse.urlencode({"client_id": CLIENT_ID, "scope": SCOPES}).encode()
    req = urllib.request.Request(
        f"{OIDC_ISSUER}/oauth2/device/code",
        data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = urllib.request.build_opener()
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        with opener.open(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SsoAuthError(f"device/code HTTP {e.code}: {e.read().decode()[:200]}")


def _poll_token(device_code: str, interval: int, expires_in: int, proxy: str | None = None, poll_timeout: int = 120) -> dict:
    deadline = time.time() + min(expires_in, poll_timeout)
    opener = urllib.request.build_opener()
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    while time.time() < deadline:
        time.sleep(interval)
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID,
            "device_code": device_code,
        }).encode()
        req = urllib.request.Request(
            f"{OIDC_ISSUER}/oauth2/token", data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with opener.open(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            error = err.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise SsoAuthError(f"token poll: {error}: {err.get('error_description','')}")
    raise SsoAuthError("token poll timeout")


def sso_to_auth_entry(sso_cookie: str, email: str = "", proxy: str | None = None) -> dict:
    """SSO → OIDC token → auth.json entry. 全 HTTP，零浏览器。"""
    s = requests.Session(impersonate="chrome120")
    s.cookies.set("sso", sso_cookie, domain=".x.ai")

    r = s.get("https://accounts.x.ai/", timeout=15)
    if "sign-in" in r.url or "sign-up" in r.url:
        raise SsoAuthError("SSO cookie 无效")

    dc = _device_code(proxy)

    try:
        s.get(dc["verification_uri_complete"], timeout=15)
        r = s.post(
            f"{OIDC_ISSUER}/oauth2/device/verify",
            data={"user_code": dc["user_code"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome120", timeout=15, allow_redirects=True,
        )
        if "consent" not in r.url:
            raise SsoAuthError(f"verify 未到 consent 页面: {r.url}")
    except SsoAuthError:
        raise
    except Exception as e:
        raise SsoAuthError(f"verify 异常: {e}")

    try:
        r = s.post(
            f"{OIDC_ISSUER}/oauth2/device/approve",
            data={"user_code": dc["user_code"], "action": "allow", "principal_type": "User", "principal_id": ""},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome120", timeout=15, allow_redirects=True,
        )
    except Exception as e:
        raise SsoAuthError(f"approve 异常: {e}")

    token = _poll_token(dc["device_code"], dc.get("interval", 5), dc.get("expires_in", 1800), proxy)

    access = token.get("access_token") or ""
    refresh = token.get("refresh_token") or ""
    payload = _decode_jwt_payload(access)
    user_id = payload.get("sub") or payload.get("principal_id") or ""
    principal_id = payload.get("principal_id") or user_id

    expires_in = int(token.get("expires_in") or 21600)
    if "exp" in payload:
        expires_at = _rfc3339_ns(float(payload["exp"]))
    else:
        expires_at = _rfc3339_ns(time.time() + expires_in)
    iat = payload.get("iat")
    create_time = _rfc3339_ns(float(iat) if iat else time.time())

    entry = {
        "key": access,
        "auth_mode": "oidc",
        "create_time": create_time,
        "user_id": user_id,
        "email": email,
        "principal_type": payload.get("principal_type", "User"),
        "principal_id": principal_id,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": CLIENT_ID,
    }
    return entry


def write_auth_file(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {AUTH_KEY: entry}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class SsoAuthService:
    """纯 HTTP SSO→OIDC 转换服务。零浏览器依赖。输出 CPA 兼容格式。"""

    def __init__(self):
        load_dotenv()
        self.enabled = os.getenv("CPA_EXPORT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
        self.auth_dir = os.getenv("CPA_AUTH_DIR", "").strip() or str(_REG_DIR / "cpa_auths")
        self.proxy = os.getenv("CPA_PROXY", "").strip() or os.getenv("PROXY", "").strip()

    def export(self, email: str, sso: str) -> dict:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "CPA_EXPORT_ENABLED=false"}
        try:
            entry = sso_to_auth_entry(sso, email=email, proxy=self.proxy or None)
        except SsoAuthError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        out_dir = Path(self.auth_dir).expanduser()
        if not out_dir.is_absolute():
            out_dir = (_REG_DIR / out_dir).resolve()
        uid = entry.get("user_id") or secrets.token_hex(4)
        # 输出 CPA 兼容格式 (cpa_xai schema)
        from cpa_xai.schema import build_cpa_xai_auth
        from cpa_xai.writer import write_cpa_xai_auth
        payload = build_cpa_xai_auth(
            email=email,
            access_token=entry["key"],
            refresh_token=entry.get("refresh_token", ""),
            sub=uid,
        )
        path = write_cpa_xai_auth(out_dir, payload)
        probe_ok = False
        try:
            pr = self._probe(entry["key"])
            probe_ok = pr.get("ok", False)
        except Exception:
            pass
        return {"ok": True, "path": str(path), "user_id": uid, "probe_ok": probe_ok}

    def _probe(self, access_token: str) -> dict:
        base = os.getenv("CPA_BASE_URL", "https://cli-chat-proxy.grok.com/v1").rstrip("/")
        url = f"{base}/models"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "grok-reg/1.0",
        }
        opener = urllib.request.build_opener()
        proxy = self.proxy
        if proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(req, timeout=15) as resp:
                body = json.loads(resp.read())
                ids = [x.get("id") for x in body.get("data") or [] if isinstance(x, dict)]
                return {"ok": "grok-4.5" in ids, "models": ids}
        except Exception as e:
            return {"ok": False, "error": str(e)}
