"""CPA xAI OIDC mint service — wraps cpa_xai for the curl_cffi register machine."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_REG_DIR = Path(__file__).resolve().parents[1]


class CpaService:
    def __init__(self):
        load_dotenv()
        self.enabled = os.getenv("CPA_EXPORT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
        self.auth_dir = os.getenv("CPA_AUTH_DIR", "").strip() or str(_REG_DIR / "cpa_auths")
        self.proxy = os.getenv("CPA_PROXY", "").strip() or os.getenv("PROXY", "").strip()
        self.headless = os.getenv("CPA_HEADLESS", "false").strip().lower() in ("1", "true", "yes")
        self.timeout = int(os.getenv("CPA_MINT_TIMEOUT", "300"))
        self.base_url = os.getenv("CPA_BASE_URL", "https://cli-chat-proxy.grok.com/v1")
        self.probe = os.getenv("CPA_PROBE", "true").strip().lower() in ("1", "true", "yes")
        self.mint_required = os.getenv("CPA_MINT_REQUIRED", "false").strip().lower() in ("1", "true", "yes")

    def export(self, email: str, password: str, sso: str = "", sso_rw: str = ""):
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "CPA_EXPORT_ENABLED=false"}
        sys.path.insert(0, str(_REG_DIR))
        try:
            from cpa_xai import mint_and_export
        except ImportError as e:
            return {"ok": False, "error": f"cpa_xai import failed: {e}"}
        out_dir = Path(self.auth_dir).expanduser()
        if not out_dir.is_absolute():
            out_dir = (_REG_DIR / out_dir).resolve()
        result = mint_and_export(
            email=email,
            password=password,
            auth_dir=out_dir,
            proxy=self.proxy or None,
            headless=self.headless,
            base_url=self.base_url,
            probe=self.probe,
            browser_timeout_sec=float(self.timeout),
            force_standalone=True,
            cookies=None,
            reuse_browser=True,
            recycle_every=15,
        )
        if not result.get("ok") and self.mint_required:
            raise RuntimeError(f"CPA mint required but failed: {result.get('error')}")
        return result
