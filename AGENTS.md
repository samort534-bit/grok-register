# AGENTS.md — Grok 批量注册工具

## 关键命令

```bash
# 1. 先启动 Solver（默认监听 http://127.0.0.1:5072）
python api_solver.py --browser_type camoufox --thread 5 --debug

# 2. 新终端运行注册（会提示输入并发数和目标数量）
python grok.py           # 静默模式，只有关键日志
python grok.py --debug   # 显示详细 debug 日志
```

Solver 额外参数：`--proxy`（读取 proxies.txt）、`--random`（随机 UA）、`--browser`/`--version`（指定指纹）、`--host`/`--port`。

## 架构

| 文件 | 职责 |
|------|------|
| `grok.py` | 主入口，多线程注册 + NSFW + CPA（可选） |
| `api_solver.py` | Quart 异步 API，Camoufox/Patchright 自动化解决 Turnstile，池化管理浏览器 |
| `TurnstileSolver.bat` | Solver 启动快捷方式（同 `api_solver.py --browser_type camoufox --thread 5 --debug`） |
| `g/` | 服务包：`EmailService`, `TurnstileService`, `UserAgreementService`, `NsfwSettingsService`, `CpaService`, `SsoAuthService`（全部从 `g.__init__` 导出） |
| `cpa_xai/` | CPA OIDC mint 的 DrissionPage 浏览器 fallback（`CpaService` 按需 import） |
| `browser_configs.py` | Patchright 浏览器指纹配置池 |
| `db_results.py` | Solver 内存结果存储 |
| `sso_to_auth_json.py` | 离线：SSO Token → OIDC auth.json（`--probe` 可探测 grok-4.5 模型） |

## 配置 `.env`

- `WORKER_DOMAIN` + `FREEMAIL_TOKEN` — freemail 必填
- `YESCAPTCHA_KEY` — 留空 = 本地 Solver，填写 = 走 YesCaptcha API
- `CPA_*` — 可选，失败不影响 SSO 结果
- **SECURITY**: `.env` 包含真实凭据且已被 `.gitignore` 忽略，注意不要手动误提交

## 注册流程（`grok.py`）

使用 `curl_cffi.requests.Session`（非标准 `requests`），impersonate 随机轮流 `chrome110`/`chrome119`/`chrome120`/`edge99`/`edge101`。与 x.ai 后端通过 **gRPC-web protobuf** 通信（`/auth_mgmt.AuthManagement/*`），邮箱验证码也走 protobuf 编码。

注册成功后依次：accept TOS → enable NSFW → enable Unhinged → CPA OIDC mint（先纯 HTTP `SsoAuthService`，失败 fallback 到 DrissionPage `CpaService`）。

`SsoAuthService` 输出 `cpa_xai` 格式（`type: xai`），文件名 `xai-<email>.json`，CPA proxy 可直接识别。

## freemail API 端点（`EmailService`）

- `GET /api/generate` — 创建邮箱，返回 `{"email": "..."}`
- `GET /api/emails?mailbox=...` — 轮询验证码，返回 `[{"verification_code": "..."}]`
- `DELETE /api/mailboxes?address=...` — 用完即删

## 输出

- SSO Token: `keys/grok_{时间戳}_{数量}.txt`（`keys/` 在 `.gitignore`）
- CPA auth: `cpa_auths/xai-<email>.json`（`cpa_xai` 格式，`type: xai`，CPA proxy 可识别）
- 单独补认证：`python sso_to_auth_json.py --sso keys/grok_*.txt --out-dir ./auth_out`，支持 `--delay` 间隔、`--proxy`、`--probe`

## 验证

无测试框架、无 lint/typecheck 配置 — 直接 `python grok.py` 运行验证。

## 依赖

安装：`pip install -r requirements.txt`（curl_cffi, beautifulsoup4, python-dotenv, requests, DrissionPage>=4.1）。Solver 额外依赖 camoufox, patchright, quart, rich。
