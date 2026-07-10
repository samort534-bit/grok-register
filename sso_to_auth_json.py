#!/usr/bin/env python3
"""SSO cookie → ~/.grok/auth.json 格式（纯 HTTP Device Flow，零浏览器）

用法:
  # 批量（读取 keys/grok_*.txt 的 SSO）
  python sso_to_auth_json.py --sso keys/grok_*.txt --out-dir ./auth_out

  # 单个 SSO
  python sso_to_auth_json.py --sso-cookie 'eyJ...' --out ~/.grok/auth.json

  # 合并输出（key 带 user_id 后缀避免覆盖）
  python sso_to_auth_json.py --sso keys/grok_*.txt --out auth_merged.json --merge
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g.sso_auth_service import sso_to_auth_entry, SsoAuthError
from cpa_xai.schema import build_cpa_xai_auth
from cpa_xai.writer import write_cpa_xai_auth
from cpa_xai.probe import probe_models


def load_sso_list(paths: list[str]) -> list[tuple[str, str]]:
    """返回 [(sso, email_or_empty)]"""
    out: list[tuple[str, str]] = []
    for p in paths:
        for p2 in Path(p).resolve().parent.glob(Path(p).name) if ("*" in p or "?" in p) else [Path(p)]:
            p2 = Path(p2).resolve()
            if not p2.is_file():
                continue
            for line in p2.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "----" in line:
                    parts = line.split("----")
                    out.append((parts[-1].strip(), parts[0].strip()))
                else:
                    out.append((line, ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SSO → auth.json (纯 HTTP, 零浏览器)")
    ap.add_argument("--sso", nargs="+", help="SSO 文件/通配符")
    ap.add_argument("--sso-cookie", help="单个 SSO JWT")
    ap.add_argument("--out", help="输出 auth.json 路径（单账号或 --merge）")
    ap.add_argument("--out-dir", default="./auth_out", help="批量输出目录")
    ap.add_argument("--merge", action="store_true", help="合并到 --out (旧格式)")
    ap.add_argument("--delay", type=int, default=3, help="每号间隔秒数")
    ap.add_argument("--proxy", default="", help="HTTP 代理")
    ap.add_argument("--probe", action="store_true", help="探测 grok-4.5 模型")
    args = ap.parse_args()

    sso_list: list[tuple[str, str]] = []
    if args.sso_cookie:
        sso_list.append((args.sso_cookie.strip(), ""))
    if args.sso:
        for pattern in args.sso:
            sso_list.extend(load_sso_list([pattern]))

    if not sso_list:
        ap.error("需要 --sso 或 --sso-cookie")

    if len(sso_list) > 1 and not args.out_dir and not args.merge:
        args.out_dir = args.out_dir or "./auth_out"

    print(f"SSO → auth.json: {len(sso_list)} 个, delay={args.delay}s")
    ok = fail = 0

    for i, (sso, email) in enumerate(sso_list, 1):
        print(f"\n[{i}/{len(sso_list)}] {email or sso[:20]}...")
        try:
            entry = sso_to_auth_entry(sso, email=email, proxy=args.proxy or None)
            uid = entry.get("user_id") or secrets.token_hex(4)

            payload = build_cpa_xai_auth(
                email=email or entry.get("email", ""),
                access_token=entry["key"],
                refresh_token=entry.get("refresh_token", ""),
                sub=uid,
            )

            if args.merge:
                # 旧格式合并（兼容）
                out_dir = Path(args.out).parent if args.out else Path(args.out_dir)
                out_dir = out_dir.expanduser().resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                from g.sso_auth_service import write_auth_file, AUTH_KEY
                path = out_dir / f"{uid}.json"
                write_auth_file(path, entry)
                print(f"  old-format -> {path}")
            elif args.out and len(sso_list) == 1:
                path = write_cpa_xai_auth(Path(args.out).parent, payload, filename=Path(args.out).name)
                print(f"  {path}")
            else:
                out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else Path("./auth_out").resolve()
                path = write_cpa_xai_auth(out_dir, payload)
                print(f"  {path}")

            if args.probe:
                pr = probe_models(entry["key"], proxy=args.proxy or None)
                has = pr.get("has_grok_45", False)
                print(f"  probe: grok-4.5={'YES' if has else 'NO'} models={pr.get('model_ids')}")

            ok += 1
            print(f"  OK user_id={uid[:12]}...")
        except SsoAuthError as e:
            fail += 1
            print(f"  FAIL: {e}")
        except Exception as e:
            fail += 1
            print(f"  EXCEPTION: {e}")

        if args.delay and i < len(sso_list):
            time.sleep(args.delay)

    print(f"\n完成: {ok}/{len(sso_list)} 成功, {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
