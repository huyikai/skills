#!/usr/bin/env python3
"""zentao17 — 自部署禅道 17.x 命令行工具（基于 v1 RESTful API，兼容禅道 12.0+）

用法示例:
  zentao17 login                          # 交互式配置服务地址与账号
  zentao17 whoami                         # 当前操作账号（工号＋姓名；actions 的 actor 是姓名）
  zentao17 products                       # 查看产品列表（获取产品 ID）
  zentao17 bugs --product 1               # 某产品的未关闭 Bug（产品级默认即未关闭口径）
  zentao17 bugs --execution 5 --status unclosed --keyword 店舗  # 状态与关键词均在客户端过滤
  zentao17 bug 123                        # Bug 详情：步骤文本＋截图清单＋actions 全历史
  zentao17 download 123 --dir /tmp/x      # 下载步骤截图与附件
  zentao17 changes --product 1 --state s.json        # 巡检预检：与上次快照比对
  zentao17 resolve 123 --comment "已在 commit abc123 修复"
  zentao17 close 123 --comment "验证通过"
  zentao17 activate 123 -c "误解决，重新激活"         # 激活（网页会话通道）
  zentao17 assign 123 --to 10267114 -c "转产品拍板"   # 转派（网页会话通道，保留原状态）
  zentao17 comment 123 -c "进展更新..."               # 独立备注（网页会话通道）
  zentao17 users --search 孙杰                        # 查账号
  zentao17 self-update                                # 从本机 skills 源仓库拉取最新版（6 小时节流）

写命令（resolve/close/activate/assign/comment）一律写后回读 actions 自验证，退出码:
  0 已生效（或目标状态已满足、未重复执行）  1 一般错误/服务端拒绝
  3 回读确认未生效（可核对后重试）          4 结果未知（网络中断且回读失败，切勿直接重试）

凭证来源优先级: 命令行参数 > 环境变量(ZENTAO_URL/ZENTAO_ACCOUNT/ZENTAO_PASSWORD) > ~/.config/zentao17/credentials.json
"""
import argparse
import getpass
import html
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "zentao17"
CRED_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
API = "/api.php/v1"

RESOLUTIONS = ["fixed", "notrepro", "bydesign", "duplicate", "external", "postponed", "willnotfix", "tostory"]
RESOLUTION_NAMES = {
    "fixed": "已解决", "notrepro": "无法重现", "bydesign": "设计如此", "duplicate": "重复Bug",
    "external": "外部原因", "postponed": "延期处理", "willnotfix": "不予解决", "tostory": "转为需求",
}

# 与 SKILL.md 开头的「CLI 版本」一致；改了命令集就同时改两处
VERSION = "2026-10-29.3"

EXIT_NOT_APPLIED = 3
EXIT_UNKNOWN = 4

# 读请求遇到这些错误自动重试；写请求不重试，交给写后回读判定。
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException)
GET_RETRIES = 3


class NetworkError(Exception):
    """连接失败、超时、连接被重置——请求是否到达服务端未知。"""


class ApiError(Exception):
    """服务端明确返回错误（HTTP 4xx/5xx）。"""


def die(msg, code=1):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


def load_creds():
    saved = {}
    if CRED_FILE.exists():
        try:
            saved = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    return {
        "url": os.environ.get("ZENTAO_URL") or saved.get("url", ""),
        "account": os.environ.get("ZENTAO_ACCOUNT") or saved.get("account", ""),
        "password": os.environ.get("ZENTAO_PASSWORD") or saved.get("password", ""),
    }


def save_creds(url, account, password):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps({"url": url, "account": account, "password": password},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    CRED_FILE.chmod(0o600)
    try:
        TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def normalize_base(url):
    url = url.strip().rstrip("/")
    for suffix in ("/api.php/v1", "/api.php", "/index.php", "/www"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if not re.match(r"^https?://", url):
        url = "http://" + url
    return url


def ssl_context(insecure):
    return ssl._create_unverified_context() if insecure else None


def http(method, base, path, body=None, token=None, insecure=False):
    url = base + API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    attempts = GET_RETRIES if method == "GET" else 1
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Token", token)
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_context(insecure)) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw
        except NETWORK_ERRORS as e:
            if attempt < attempts:
                time.sleep(1.5 * attempt)
                continue
            reason = getattr(e, "reason", None) or e
            raise NetworkError(f"{method} {url}: {reason}")


def fetch_token(creds, insecure, force=False):
    if not force and TOKEN_FILE.exists():
        try:
            cache = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if cache.get("base") == creds["url"] and cache.get("account") == creds["account"] and cache.get("token"):
                return cache["token"]
        except Exception:
            pass
    if not (creds["url"] and creds["account"] and creds["password"]):
        die("尚未配置登录信息。请先执行: zentao17 login\n"
            "或设置环境变量 ZENTAO_URL / ZENTAO_ACCOUNT / ZENTAO_PASSWORD")
    status, data = http("POST", creds["url"], "/tokens",
                        {"account": creds["account"], "password": creds["password"]}, insecure=insecure)
    token = data.get("token") if isinstance(data, dict) else None
    if status != 201 and status != 200 or not token:
        msg = data if isinstance(data, str) else (data or {})
        die(f"登录失败(HTTP {status}): {msg}\n"
            "请确认: 1) 账号密码正确  2) 地址格式如 http://IP:端口/zentao  3) 禅道版本 >= 12.0")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"base": creds["url"], "account": creds["account"], "token": token}),
                          encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return token


def api(method, path, body=None, params=None, insecure=False):
    creds = load_creds()
    if not creds["url"]:
        die("尚未配置禅道地址。请先执行: zentao17 login")
    if params:
        path = path + "?" + urllib.parse.urlencode(params)
    token = fetch_token(creds, insecure)
    status, data = http(method, creds["url"], path, body, token, insecure)
    if status == 401:  # token 过期，重新登录一次
        token = fetch_token(creds, insecure, force=True)
        status, data = http(method, creds["url"], path, body, token, insecure)
    if status == 404:
        raise ApiError(f"接口不存在(HTTP 404): {path}\n请确认禅道版本支持 v1 API(>= 12.0)")
    if status == 403:
        raise ApiError("无权限(HTTP 403): 当前账号对目标对象/产品没有操作权限")
    if status >= 400:
        msg = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        raise ApiError(f"请求失败(HTTP {status}): {msg}")
    return data


def vwidth(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def table(rows, headers):
    if not rows:
        print("(无数据)")
        return
    widths = [vwidth(h) for h in headers]
    cells = []
    for row in rows:
        line = [str(row.get(h, "")) for h in headers]
        cells.append(line)
        for i, c in enumerate(line):
            widths[i] = max(widths[i], vwidth(c))
    def fmt(line):
        return "  ".join(c + " " * (widths[i] - vwidth(c)) for i, c in enumerate(line))
    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for line in cells:
        print(fmt(line))


IMG_RE = re.compile(r"<img\b[^>]*?\bsrc=['\"]([^'\"]+)['\"][^>]*>", re.I)


def strip_html(text, images=None):
    """HTML → 纯文本。传入 images 列表时，<img> 替换为 [图N] 并把 src 依次追加进列表。"""
    if not text:
        return ""
    def img(m):
        if images is None:
            return ""
        images.append(m.group(1))
        return f"[图{len(images)}]"
    text = IMG_RE.sub(img, text)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [ln.rstrip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def norm_text(text):
    """比对用：去标签、反转义、折叠空白。"""
    return re.sub(r"\s+", " ", strip_html(text or "")).strip()


def account_of(user):
    if isinstance(user, dict):
        return str(user.get("account") or "")
    return str(user or "")


def who(user):
    if isinstance(user, dict):
        return user.get("realname") or user.get("account") or ""
    return str(user or "")


def who_full(user):
    if isinstance(user, dict):
        name, acc = user.get("realname"), user.get("account")
        return f"{name}({acc})" if name and acc else (name or acc or "")
    return str(user or "")


def get_bug(bug_id, insecure):
    data = api("GET", f"/bugs/{bug_id}", insecure=insecure)
    return data.get("bug", data) if isinstance(data, dict) else {}


def bug_actions(bug):
    acts = bug.get("actions") or []
    return sorted(acts, key=lambda a: int(a.get("id") or 0))


def last_action_id(bug):
    return max((int(a.get("id") or 0) for a in bug.get("actions") or []), default=0)


_ME = {}


def whoami(insecure):
    if not _ME:
        data = api("GET", "/user", insecure=insecure)
        prof = data.get("profile", data) if isinstance(data, dict) else {}
        _ME.update(prof or {})
    return _ME


def cmd_login(args):
    creds = load_creds()
    url = args.url or input(f"禅道地址(如 http://IP:端口/zentao){'，回车沿用 ' + creds['url'] if creds['url'] else ''}: ").strip() or creds["url"]
    if not url:
        die("必须提供禅道地址")
    url = normalize_base(url)
    account = args.account or input(f"账号{'，回车沿用 ' + creds['account'] if creds['account'] else ''}: ").strip() or creds["account"]
    if not account:
        die("必须提供账号")
    password = args.password or getpass.getpass("密码: ")
    save_creds(url, account, password)
    fetch_token({"url": url, "account": account, "password": password}, args.insecure, force=True)
    print(f"登录成功，凭证已保存到 {CRED_FILE}")


def cmd_whoami(args):
    me = whoami(args.insecure)
    role = me.get("role")
    role = role.get("name") if isinstance(role, dict) else role
    if args.json:
        print(json.dumps({"id": me.get("id"), "account": me.get("account"), "realname": me.get("realname"),
                          "role": role}, ensure_ascii=False))
        return
    print(f"{me.get('account')} {me.get('realname')}" + (f"（{role}）" if role else ""))


def cmd_users(args):
    users, page = [], 1
    while page < 100:
        data = api("GET", "/users", params={"limit": 100, "page": page}, insecure=args.insecure)
        batch = data.get("users", []) if isinstance(data, dict) else []
        users.extend(batch)
        if not batch or len(users) >= int(data.get("total") or 0):
            break
        page += 1
    if args.search:
        kw = args.search.lower()
        users = [u for u in users if kw in str(u.get("account", "")).lower() or kw in str(u.get("realname", "")).lower()]
    if args.json:
        print(json.dumps(users, ensure_ascii=False, indent=2))
        return
    table([{"账号": u.get("account"), "姓名": u.get("realname"),
            "角色": (u.get("role") or {}).get("name") if isinstance(u.get("role"), dict) else u.get("role", "")}
           for u in users], ["账号", "姓名", "角色"])


def cmd_products(args):
    data = api("GET", "/products", params={"limit": 100}, insecure=args.insecure)
    products = data.get("products", []) if isinstance(data, dict) else data
    rows = [{"ID": p.get("id"), "名称": p.get("name"), "状态": p.get("status"), "代号": p.get("code", "")} for p in products]
    if args.json:
        print(json.dumps(products, ensure_ascii=False, indent=2))
    else:
        table(rows, ["ID", "名称", "状态", "代号"])


def cmd_projects(args):
    params = {"order": "order_asc", "limit": args.limit, "page": args.page}
    if args.status:
        params["status"] = args.status
    data = api("GET", "/projects", params=params, insecure=args.insecure)
    projects = data.get("projects", []) if isinstance(data, dict) else []
    if args.all:
        total = int(data.get("total", len(projects)) or 0)
        page = args.page
        while projects and len(projects) < total and page < 100:
            page += 1
            params["page"] = page
            more = api("GET", "/projects", params=params, insecure=args.insecure)
            batch = more.get("projects", []) if isinstance(more, dict) else []
            if not batch:
                break
            projects.extend(batch)
    if args.search:
        kw = args.search.lower()
        projects = [p for p in projects if kw in str(p.get("name", "")).lower() or kw in str(p.get("code", "")).lower()]
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2))
        return
    print(f"共 {len(projects)} 个项目")
    table([{"ID": p.get("id"), "名称": p.get("name"), "编号": p.get("code", ""), "状态": p.get("status")} for p in projects],
          ["ID", "名称", "编号", "状态"])


def cmd_executions(args):
    params = {"order": "id_desc", "limit": args.limit, "page": args.page}
    if args.status:
        params["status"] = args.status
    path = f"/projects/{args.project}/executions" if args.project else "/executions"
    data = api("GET", path, params=params, insecure=args.insecure)
    exs = data.get("executions", []) if isinstance(data, dict) else []
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"共 {data.get('total', len(exs))} 个执行（第 {data.get('page', args.page)} 页）")
    table([{"ID": e.get("id"), "名称": e.get("name"), "编号": e.get("code", ""), "状态": e.get("status"),
            "起止": f"{e.get('begin', '')} ~ {e.get('end', '')}"} for e in exs],
          ["ID", "名称", "编号", "状态", "起止"])


# ---------------------------------------------------------------------------
# Bug 列表
# 实测（2026-10-28，禅道 17.8）：执行级 /executions/{id}/bugs 忽略 status 参数（unclosed 也返回已关闭单）；
# 产品级 /bugs?product= 默认只返回未关闭单，status=all 才含已关闭；列表的 status 会出现 confirmed
# （＝激活且已确认，详情接口对同一单返回 active）。因此状态与「我的」类筛选一律在客户端执行。
STATE_FILTERS = {
    "unclosed": lambda b: b.get("status") != "closed",
    "active": lambda b: b.get("status") in ("active", "confirmed"),
    "confirmed": lambda b: b.get("status") == "confirmed" or str(b.get("confirmed")) == "1" and b.get("status") == "active",
    "resolved": lambda b: b.get("status") == "resolved",
    "closed": lambda b: b.get("status") == "closed",
}
MINE_FILTERS = {"assignedtome": "assignedTo", "openedbyme": "openedBy", "resolvedbyme": "resolvedBy"}


def list_bugs(scope, scope_id, status=None, order="id_desc", limit=20, page=1, all_pages=False,
              keywords=None, assigned=None, insecure=False):
    """返回 (bugs, total, notes)。有客户端筛选时自动取全部页，保证结果完整。"""
    params = {"order": order}
    if scope == "execution":
        path = f"/executions/{scope_id}/bugs"
    elif scope == "project":
        path = f"/projects/{scope_id}/bugs"
    else:
        path = "/bugs"
        params["product"] = scope_id
    notes = []
    client_filter = bool(keywords or assigned or (status and status != "all"))
    if status == "all" or status == "closed":
        params["status"] = "all"
    elif status and status not in STATE_FILTERS and status not in MINE_FILTERS:
        params["status"] = status  # 未知筛选值原样交给服务端
    if client_filter:
        all_pages = True
        notes.append("筛选在客户端执行，已取全部页")
    params["limit"] = max(limit, 100) if all_pages else limit
    params["page"] = page
    data = api("GET", path, params=params, insecure=insecure)
    bugs = data.get("bugs", []) if isinstance(data, dict) else []
    total = int(data.get("total", len(bugs)) or 0) if isinstance(data, dict) else len(bugs)
    if all_pages:
        cur = page
        while bugs and len(bugs) < total and cur < 100:
            cur += 1
            params["page"] = cur
            more = api("GET", path, params=params, insecure=insecure)
            batch = more.get("bugs", []) if isinstance(more, dict) else []
            if not batch:
                break
            bugs.extend(batch)
        total = len(bugs)
    if status in STATE_FILTERS:
        bugs = [b for b in bugs if STATE_FILTERS[status](b)]
    elif status in MINE_FILTERS:
        me = account_of(whoami(insecure))
        field = MINE_FILTERS[status]
        bugs = [b for b in bugs if account_of(b.get(field)) == me and b.get("status") != "closed"]
    if assigned:
        target = account_of(whoami(insecure)) if assigned == "me" else assigned
        bugs = [b for b in bugs if account_of(b.get("assignedTo")) == target]
    if keywords:
        kws = [k.lower() for k in keywords]
        bugs = [b for b in bugs if any(k in str(b.get("title", "")).lower() for k in kws)]
    if client_filter:
        total = len(bugs)
    return bugs, total, notes


def status_label(b):
    st = b.get("status")
    if st == "confirmed":
        return "激活(已确认)"
    return b.get("statusName") or st


def cmd_bugs(args):
    if args.execution:
        scope, sid = "execution", args.execution
    elif args.project:
        scope, sid = "project", args.project
    elif args.product:
        scope, sid = "product", args.product
    else:
        die("必须指定范围: --execution <执行ID> / --project <项目ID> / --product <产品ID>")
    bugs, total, notes = list_bugs(scope, sid, args.status, args.order, args.limit, args.page, args.all,
                                   args.keyword, args.assigned, args.insecure)
    if args.json:
        print(json.dumps({"page": 1 if args.all or notes else args.page, "total": total, "limit": len(bugs),
                          "notes": notes, "bugs": bugs}, ensure_ascii=False, indent=2))
        return
    head = f"共 {total} 条"
    head += f"（{'；'.join(notes)}）" if notes else ("（已取全部页）" if args.all else f"（第 {args.page} 页，每页 {args.limit} 条）")
    print(head)
    rows = [{"ID": b.get("id"), "状态": status_label(b), "严重": b.get("severity"),
             "优先": b.get("pri"), "指派给": who(b.get("assignedTo")), "创建人": who(b.get("openedBy")),
             "标题": b.get("title")}
            for b in bugs]
    table(rows, ["ID", "状态", "严重", "优先", "指派给", "创建人", "标题"])


# ---------------------------------------------------------------------------
# Bug 详情：一次给全「步骤文本＋截图清单＋附件＋actions 全历史」，不必再读 --json
def bug_media(bug, base):
    images = []
    steps = strip_html(bug.get("steps") or "", images)
    urls = [urllib.parse.urljoin(base + "/", src) for src in images]
    files = []
    for f in bug.get("files") or []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        ext = f.get("extension") or "bin"
        files.append({"title": f.get("title") or f"file-{f['id']}.{ext}",
                      "url": urllib.parse.urljoin(base + "/", f"file-read-{f['id']}.{ext}")})
    return steps, urls, files


HISTORY_NOISE = {"resolvedBuild", "resolvedBy", "activatedCount", "lastEditedBy", "closedBy"}


def format_history(hist):
    """只列影响判断的字段变化；日期、计数、解决人（＝actor）等随动字段略去。"""
    if isinstance(hist, str):
        try:
            hist = json.loads(hist)
        except Exception:
            return []
    out = []
    for h in hist or []:
        if not isinstance(h, dict):
            continue
        field = h.get("field")
        if field in HISTORY_NOISE or str(field).endswith("Date"):
            continue
        if field in ("steps",):
            out.append(f"{field} 已修改")
            continue
        old, new = norm_text(str(h.get("old") or "")), norm_text(str(h.get("new") or ""))
        out.append(f"{field}: {old[:60] or '∅'} → {new[:60] or '∅'}")
    return out


def cmd_bug(args):
    bug = get_bug(args.id, args.insecure)
    if args.json:
        print(json.dumps(bug, ensure_ascii=False, indent=2))
        return
    base = load_creds()["url"]
    steps, images, files = bug_media(bug, base)
    print(f"#{bug.get('id')} {bug.get('title')}")
    print(f"状态: {bug.get('status')}" + (f"（已确认）" if str(bug.get("confirmed")) == "1" and bug.get("status") == "active" else "")
          + f"  严重: {bug.get('severity')}  优先: {bug.get('pri')}  类型: {bug.get('type')}"
          + f"  激活次数: {bug.get('activatedCount') or 0}")
    print(f"指派给: {who_full(bug.get('assignedTo'))}  创建: {who_full(bug.get('openedBy'))} {bug.get('openedDate') or ''}")
    if bug.get("resolvedBy"):
        print(f"解决: {who_full(bug.get('resolvedBy'))} {bug.get('resolution') or ''} {bug.get('resolvedDate') or ''}")
    if bug.get("closedBy"):
        print(f"关闭: {who_full(bug.get('closedBy'))} {bug.get('closedDate') or ''}")
    scope = [f"产品 {bug.get('product')} {bug.get('productName') or ''}".strip()]
    if bug.get("execution"):
        scope.append(f"执行 {bug.get('execution')} {bug.get('executionName') or ''}".strip())
    if bug.get("moduleTitle"):
        scope.append(f"模块 {bug.get('moduleTitle')}")
    print("所属: " + " / ".join(scope))
    print("\n--- 步骤 ---")
    print(steps or "(空)")
    if images or files:
        print(f"\n--- 截图/附件（定性前必须核对；zentao17 download {bug.get('id')} 可下载）---")
        for i, u in enumerate(images, 1):
            print(f"[图{i}] {u}")
        for f in files:
            print(f"[附件] {f['title']} {f['url']}")
    acts = bug_actions(bug)
    print(f"\n--- 历史 actions（{len(acts)} 条，按时间；actor 为姓名）---")
    for a in acts:
        extra = f" → {a.get('extra')}" if a.get("extra") else ""
        print(f"#{a.get('id')} {a.get('date')} {a.get('actor')} {a.get('action')}{extra}")
        for line in format_history(a.get("history")):
            print(f"    改动 {line}")
        comment = strip_html(a.get("comment") or "")
        if comment:
            print("    备注: " + comment.replace("\n", "\n          "))


def download(url, dest, insecure):
    """附件实测无需登录即可读取；拿到 HTML（被要求登录）时改用网页会话重取。"""
    for attempt in range(1, GET_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "zentao17-cli/1.2"})
            with urllib.request.urlopen(req, timeout=60, context=ssl_context(insecure)) as resp:
                ctype, data = resp.headers.get("Content-Type", ""), resp.read()
            break
        except NETWORK_ERRORS as e:
            if attempt == GET_RETRIES:
                raise NetworkError(f"GET {url}: {getattr(e, 'reason', None) or e}")
            time.sleep(1.5 * attempt)
    if "text/html" in ctype:
        creds = load_creds()
        client = web_client(creds, insecure)
        path = urllib.parse.urlsplit(url).path
        status, _, data = client.request(path, raw=True)
        if status != 200 or data[:15].lower().startswith(b"<!doctype html") or data[:5].lower() == b"<html":
            raise ApiError(f"附件下载失败（HTTP {status}）：{url}")
    dest.write_bytes(data)
    return len(data)


def cmd_download(args):
    bug = get_bug(args.id, args.insecure)
    base = load_creds()["url"]
    _, images, files = bug_media(bug, base)
    if not images and not files:
        print(f"Bug #{args.id} 没有截图或附件")
        return
    out = Path(args.dir) if args.dir else Path(tempfile.gettempdir()) / "zentao17-files" / str(args.id)
    out.mkdir(parents=True, exist_ok=True)
    targets = [(f"图{i}", u, Path(urllib.parse.urlsplit(u).path).name) for i, u in enumerate(images, 1)]
    targets += [("附件", f["url"], re.sub(r"[/\\]", "_", f["title"])) for f in files]
    for label, url, name in targets:
        dest = out / (f"{label}-{name}" if label != "附件" else name)
        size = download(url, dest, args.insecure)
        print(f"[{label}] {dest}（{size} 字节）")


# ---------------------------------------------------------------------------
# 巡检预检：与上次快照比对，没有新单、跟踪单也没有新动作时，调用方可直接结束本轮。
def fingerprint(bug):
    return {"status": bug.get("status"), "assignedTo": account_of(bug.get("assignedTo")),
            "lastAction": last_action_id(bug)}


def parse_ids(text):
    return [int(x) for x in re.split(r"[,\s]+", text or "") if x.strip()]


def cmd_changes(args):
    state_path = Path(args.state)
    first = not state_path.exists()
    state = {"version": 1, "product": args.product, "seen": [], "tracked": {}}
    if not first:
        try:
            state.update(json.loads(state_path.read_text(encoding="utf-8")))
        except Exception as e:
            die(f"快照文件无法解析（{e}）：{state_path}。删除后重跑即重建基线。")
    tracked = {int(k): v for k, v in (state.get("tracked") or {}).items()}
    if args.set_tracked is not None:
        keep = set(parse_ids(args.set_tracked))
        tracked = {i: tracked.get(i) for i in keep}
    for i in parse_ids(args.track):
        tracked.setdefault(i, None)
    for i in parse_ids(args.untrack):
        tracked.pop(i, None)
    seen = set(int(x) for x in state.get("seen") or [])

    bugs, total, _ = list_bugs("product", args.product, "unclosed", all_pages=True, insecure=args.insecure)
    unclosed = {int(b["id"]): b for b in bugs}
    kws = [k.lower() for k in args.keyword or []]
    new = [unclosed[i] for i in sorted(unclosed, reverse=True) if i not in seen]

    # --own-prefix：actor 是本人姓名、且备注以该前缀开头的动作（自动代理自己的写操作）不算变化
    me_name = whoami(args.insecure).get("realname") if args.own_prefix else None
    prefix = norm_text(args.own_prefix) if args.own_prefix else None

    def own(a):
        return bool(prefix) and a.get("actor") == me_name and norm_text(a.get("comment")).startswith(prefix)

    changed, gone, baseline, own_only, fps = [], [], [], [], {}
    for i in sorted(tracked):
        bug = get_bug(i, args.insecure)
        fp = fingerprint(bug)
        fps[i] = fp
        old = tracked.get(i)
        if fp["status"] == "closed":
            gone.append((i, bug))
        elif old is None:
            baseline.append(i)
        elif fp != old:
            fresh = [a for a in bug_actions(bug) if int(a.get("id") or 0) > int(old.get("lastAction") or 0)]
            if fresh and all(own(a) for a in fresh):
                own_only.append(i)
            else:
                changed.append((i, old, fp, fresh))

    result = {"first": first, "unclosed": len(unclosed),
              "new": [{"id": int(b["id"]), "title": b.get("title"), "assignedTo": account_of(b.get("assignedTo")),
                       "keywordHit": any(k in str(b.get("title", "")).lower() for k in kws)} for b in new],
              "changed": [{"id": i, "before": o, "after": f,
                           "actions": [{"id": a.get("id"), "date": a.get("date"), "actor": a.get("actor"),
                                        "action": a.get("action"), "extra": a.get("extra"),
                                        "comment": norm_text(a.get("comment"))[:120]} for a in acts]}
                          for i, o, f, acts in changed],
              "closed": [i for i, _ in gone], "baseline": baseline, "ownOnly": own_only}
    has_change = bool(new or changed or gone)
    result["result"] = "changed" if has_change else "none"

    if args.update:
        seen |= set(unclosed)
        state.update({"product": args.product, "seen": sorted(seen),
                      "tracked": {str(i): fps[i] for i in sorted(fps) if i not in dict(gone)}})
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_name(state_path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, state_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"预检 产品{args.product}：未关闭 {len(unclosed)} 条；新出现 {len(new)} 条；"
          f"跟踪 {len(tracked)} 条中变化 {len(changed)}、已关闭 {len(gone)}、新纳入基线 {len(baseline)}"
          + (f"、仅本方动作 {len(own_only)}（不计）" if own_only else "")
          + ("（首次运行，全部视为新出现）" if first else ""))
    for item in result["new"]:
        hit = " [关键词命中]" if item["keywordHit"] else ""
        print(f"NEW {item['id']}{hit} 指派 {item['assignedTo']} {item['title']}")
    for i, o, f, acts in changed:
        print(f"CHANGED {i} {o.get('status')}→{f['status']} 指派 {o.get('assignedTo')}→{f['assignedTo']}")
        for a in acts:
            c = norm_text(a.get("comment"))[:80]
            print(f"    #{a.get('id')} {a.get('date')} {a.get('actor')} {a.get('action')}"
                  + (f" → {a.get('extra')}" if a.get("extra") else "") + (f"：{c}" if c else ""))
    for i, bug in gone:
        print(f"CLOSED {i} {bug.get('title')}")
    print(f"RESULT: {result['result']}" + ("（已更新快照）" if args.update else ""))


# ---------------------------------------------------------------------------
# 写命令：写后回读自验证。超时不等于失败——一律以回读的 actions 为准，避免重复写。
def run_write(args, label, do_write, check, skip=None):
    before = get_bug(args.id, args.insecure)
    if skip:
        msg = skip(before)
        if msg:
            print(f"Bug #{args.id} {msg}，未重复执行")
            return
    before_max = last_action_id(before)
    failure = None
    try:
        do_write(before)
    except NetworkError as e:
        failure = ("network", str(e))
    except ApiError as e:
        failure = ("api", str(e))
    try:
        after = get_bug(args.id, args.insecure)
    except (NetworkError, ApiError) as e:
        state = "写请求中途出错" if failure else "写请求已发出"
        die(f"Bug #{args.id} {label}结果未知：{state}，回读失败（{e}）。\n"
            f"请稍后执行 zentao17 bug {args.id} 核对 actions，确认未生效后再重试；切勿直接重试。", EXIT_UNKNOWN)
    fresh = [a for a in bug_actions(after) if int(a.get("id") or 0) > before_max]
    ok, note = check(before, after, fresh)
    if ok:
        msg = f"Bug #{args.id} {note}"
        if failure:
            msg += f"（写请求曾报错：{failure[1]}；回读确认已生效，勿重试）"
        print(msg)
        return
    if failure and failure[0] == "api":
        die(failure[1])
    reason = f"写请求网络中断（{failure[1]}）" if failure else "服务端已响应"
    die(f"Bug #{args.id} {label}未生效：{reason}，回读未见对应变化（{note}）。核对后可重试。", EXIT_NOT_APPLIED)


def has_action(fresh, *names):
    return any(a.get("action") in names for a in fresh)


def cmd_resolve(args):
    if args.resolution not in RESOLUTIONS:
        die(f"无效的解决方案: {args.resolution}，可选: {', '.join(RESOLUTIONS)}")
    if args.resolution == "duplicate" and not args.duplicate_bug:
        die("resolution=duplicate 时必须给出 --duplicate-bug")

    def skip(bug):
        if bug.get("status") == "closed":
            die(f"Bug #{args.id} 已关闭，不能 resolve")
        if bug.get("status") == "resolved":
            return f"已是已解决状态（{bug.get('resolution')}，by {who(bug.get('resolvedBy'))}）"

    def write(_):
        body = {"resolution": args.resolution, "resolvedBuild": args.build, "comment": args.comment or ""}
        if args.assign:
            body["assignedTo"] = args.assign
        if args.resolution == "duplicate":
            body["duplicateBug"] = args.duplicate_bug
        api("POST", f"/bugs/{args.id}/resolve", body=body, insecure=args.insecure)

    def check(_, after, fresh):
        ok = after.get("status") == "resolved" and has_action(fresh, "resolved")
        return ok, (f"已解决（{RESOLUTION_NAMES[args.resolution]}），指派给 {who(after.get('assignedTo'))}"
                    + (f"，备注: {args.comment}" if args.comment else "")) if ok else f"当前状态 {after.get('status')}"

    run_write(args, "解决", write, check, skip)


def cmd_close(args):
    def skip(bug):
        if bug.get("status") == "closed":
            return "已是已关闭状态"

    def write(_):
        api("POST", f"/bugs/{args.id}/close", body={"comment": args.comment or ""}, insecure=args.insecure)

    def check(_, after, fresh):
        ok = after.get("status") == "closed" and has_action(fresh, "closed")
        return ok, ("已关闭" + (f"，备注: {args.comment}" if args.comment else "")) if ok else f"当前状态 {after.get('status')}"

    run_write(args, "关闭", write, check, skip)


# ---------------------------------------------------------------------------
# 网页会话通道（activate / assign / comment）
# v1 API 在禅道 17.8 上不开放激活、独立备注与转派（activate 路由 404；PUT /bugs/{id} 忽略
# assignedTo/comment），改走网页路由：bug-activate / bug-assignTo / action-comment，
# 用 user-login 换 zentaosid 会话。凭证复用 login 已存的本地配置，不新增交互。
WEB_COOKIE_FILE = CONFIG_DIR / "web-session.json"


class WebClient:
    """带 Cookie 的网页请求客户端。opener 会跟随重定向，登录态以 /my.html 是否含「退出」判定。"""

    def __init__(self, base, insecure=False):
        self.base = base
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if insecure:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, path, data=None, referer=None, xhr=False, raw=False):
        """data 可为 dict 或 (键, 值) 列表（多值字段如 openedBuild[]）。GET 自动重试，POST 不重试。"""
        url = self.base + path
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        method = "POST" if data is not None else "GET"
        attempts = GET_RETRIES if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            # 禅道 17 反 CSRF：无 Referer 的登录后请求会被渲染成访客首页。
            req.add_header("Referer", self.base + (referer if referer else "/"))
            if xhr:
                req.add_header("X-Requested-With", "XMLHttpRequest")
            req.add_header("User-Agent", "Mozilla/5.0 zentao17-cli/1.2")
            try:
                with self.opener.open(req, timeout=30) as resp:
                    content = resp.read()
                    return resp.status, resp.geturl(), content if raw else content.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                content = e.read()
                return e.code, url, content if raw else content.decode("utf-8", "replace")
            except NETWORK_ERRORS as e:
                if attempt < attempts:
                    time.sleep(1.5 * attempt)
                    continue
                raise NetworkError(f"{method} {url}: {getattr(e, 'reason', None) or e}")

    def cookies(self):
        return {c.name: c.value for c in self.jar}


def web_logged_in(client):
    """会话有效性判据：登录用户页含「退出」，访客首页没有（访客也会被渲染成首页而非登录页）。"""
    status, final, body = client.request("/my.html")
    return status == 200 and ("退出" in (body or "")) and len(body or "") > 1000


def web_login(creds, insecure=False):
    client = WebClient(creds["url"], insecure)
    # 先 GET 一次拿初始 zentaosid，再提交登录表单。
    # 禅道 17 实测要点：登录 POST 必须带 X-Requested-With（AJAX）才返回 success JSON，
    # 否则静默无效；后续页面请求必须带 Referer，否则被渲染成访客首页。
    client.request("/user-login.html")
    status, final_url, body = client.request(
        "/user-login.html",
        data={"account": creds["account"], "password": creds["password"], "referer": "/"},
        referer="/user-login.html",
        xhr=True,
    )
    ok = isinstance(body, str) and '"success"' in body[:200]
    if not ok or not web_logged_in(client):
        hint = re.sub(r"<[^>]+>", "", (body or ""))[:120]
        die(f"网页会话登录失败（{hint}）。请核对账号密码，或禅道开启了验证码/密码加密策略。")
    cookies = client.cookies()
    sid = cookies.get("zentaosid") or next(iter(cookies.values()), "")
    if not sid:
        die("网页会话登录失败：未取得 zentaosid Cookie。")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WEB_COOKIE_FILE.write_text(
        json.dumps({"base": creds["url"], "account": creds["account"], "zentaosid": sid}),
        encoding="utf-8")
    WEB_COOKIE_FILE.chmod(0o600)
    return client


def web_client(creds, insecure=False, force_login=False):
    """优先复用已存网页会话；失效（被跳回登录页）则自动重登一次。"""
    if not (creds["url"] and creds["account"] and creds["password"]):
        die("尚未配置登录信息。请先执行: zentao17 login")
    if not force_login and WEB_COOKIE_FILE.exists():
        try:
            saved = json.loads(WEB_COOKIE_FILE.read_text(encoding="utf-8"))
            if saved.get("base") == creds["url"] and saved.get("account") == creds["account"]:
                import http.cookiejar
                client = WebClient(creds["url"], insecure)
                client.jar.set_cookie(http.cookiejar.Cookie(
                    version=0, name="zentaosid", value=saved["zentaosid"],
                    port=None, port_specified=False, domain="", domain_specified=False,
                    domain_initial_dot=False, path="/", path_specified=True, secure=False,
                    expires=None, discard=True, comment=None, comment_url=None,
                    rest={"HttpOnly": None}, rfc2109=False))
                if web_logged_in(client):
                    return client
        except NetworkError:
            raise
        except Exception:
            pass
    return web_login(creds, insecure)


def web_form(creds, insecure, form_path, view_path):
    """取弹窗表单（onlybody 仅为展示参数），会话失效时重登一次。"""
    client = web_client(creds, insecure)
    status, _, body = client.request(f"{form_path}?onlybody=yes", referer=view_path)
    if status != 200 or len(body or "") < 200:
        client = web_login(creds, insecure)
        status, _, body = client.request(f"{form_path}?onlybody=yes", referer=view_path)
    if len(body or "") < 200:
        raise ApiError(f"表单获取失败（HTTP {status}）：{form_path}，请确认 Bug 编号与账号权限")
    return client, body


def form_input_value(body, name):
    for tag in re.findall(r"<input\b[^>]*>", body or ""):
        if re.search(rf"\bname=['\"]{re.escape(name)}['\"]", tag):
            m = re.search(r"\bvalue=['\"]([^'\"]*)['\"]", tag)
            return html.unescape(m.group(1)) if m else ""
    return None


def form_selected(body, name):
    m = re.search(rf"<select\b[^>]*\bname=['\"]{re.escape(name)}['\"][\s\S]*?</select>", body or "")
    if not m:
        return []
    return [html.unescape(v) for v in re.findall(r"<option\b[^>]*\bvalue=['\"]([^'\"]*)['\"][^>]*\bselected", m.group(0)) if v]


def cmd_activate(args):
    creds = load_creds()
    form_path, view_path = f"/bug-activate-{args.id}.html", f"/bug-view-{args.id}.html"

    def skip(bug):
        if bug.get("status") == "active":
            return "已是激活状态"

    def write(_):
        client, body = web_form(creds, args.insecure, form_path, view_path)
        # 表单默认：指派给上次解决人、影响版本沿用原值；status 为隐藏字段 active。
        assigned = args.assign or (form_selected(body, "assignedTo") or [""])[0]
        builds = [args.build] if args.build else (form_selected(body, "openedBuild[]") or ["trunk"])
        data = [("assignedTo", assigned), ("status", "active"), ("comment", args.comment or "")]
        data += [("openedBuild[]", b) for b in builds]
        status, _, _ = client.request(form_path, data=data, referer=view_path)
        if status >= 400:
            raise ApiError(f"激活请求失败(HTTP {status})，请确认账号有该 Bug 的编辑权限")

    def check(_, after, fresh):
        ok = after.get("status") == "active" and has_action(fresh, "activated")
        return ok, (f"已激活，指派给 {who(after.get('assignedTo'))}"
                    + (f"，备注: {args.comment}" if args.comment else "")) if ok else f"当前状态 {after.get('status')}"

    run_write(args, "激活", write, check, skip)


def cmd_assign(args):
    creds = load_creds()
    form_path, view_path = f"/bug-assignTo-{args.id}.html", f"/bug-view-{args.id}.html"

    def write(before):
        client, body = web_form(creds, args.insecure, form_path, view_path)
        # 表单的隐藏字段 status 是单子当前状态，必须原样回传；
        # 写死 active 会把已解决的单在转派时悄悄改回激活（2026-10-28 实测表单结构）。
        keep = form_input_value(body, "status") or before.get("status") or "active"
        status, _, _ = client.request(
            form_path,
            data={"assignedTo": args.to, "status": keep, "mailto[]": "", "comment": args.comment or ""},
            referer=view_path,
        )
        if status >= 400:
            raise ApiError(f"转派请求失败(HTTP {status})，请确认账号有该 Bug 的编辑权限")

    def check(before, after, fresh):
        assigned = account_of(after.get("assignedTo"))
        if assigned != str(args.to) or not fresh:
            return False, f"当前仍指派给 {assigned or '?'}"
        note = f"已转派给 {who_full(after.get('assignedTo'))}" + (f"，备注: {args.comment}" if args.comment else "")
        if after.get("status") != before.get("status"):
            note += f"（注意：状态 {before.get('status')}→{after.get('status')}）"
        return True, note

    run_write(args, "转派", write, check)


def cmd_comment(args):
    creds = load_creds()
    view_path = f"/bug-view-{args.id}.html"
    head = norm_text(args.comment)[:30]
    if not head:
        die("备注内容不能为空")

    def write(_):
        client = web_client(creds, args.insecure)
        # 禅道 17 备注路由：action-comment-bug-{id}.html（bug-view 页备注表单的 action）。
        status, _, _ = client.request(f"/action-comment-bug-{args.id}.html", data={"comment": args.comment},
                                      referer=view_path, xhr=True)
        if status >= 400:
            raise ApiError(f"备注请求失败(HTTP {status})，请确认账号有该 Bug 的查看/备注权限")

    def check(_, after, fresh):
        # 只认本次新增的 action，旧的同文备注不算；禅道会转义/包裹 HTML，比对前先归一化。
        hit = [a for a in fresh if head in norm_text(a.get("comment"))]
        if not hit:
            return False, "actions 未检到新备注"
        return True, f"已添加备注（by {hit[-1].get('actor')}）: {args.comment[:50]}..."

    run_write(args, "备注", write, check)


# ---------- 自更新：从本机 skills 源仓库（git 克隆）拉取最新版，同步到部署副本 ----------
SKILL_DIR = Path(__file__).resolve().parent.parent
SOURCE_MARK = SKILL_DIR / ".source-repo"   # 源仓库位置，首次找到后记住
CHECK_STAMP = SKILL_DIR / ".update-check"  # 上次成功检查的时间，用于节流
UPDATE_INTERVAL = 6 * 3600
KEEP_FILES = {"LOCAL.md", ".source-repo", ".update-check"}  # 本机私有，同步时不删不改
IGNORED = {"__pycache__", ".DS_Store"}
SCAN_PRUNE = {"node_modules", "Library", "AppData", "Applications", "Pictures", "Music", "Movies", "venv"}


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def is_source_repo(path):
    path = Path(path)
    return (path / "skills" / "zentao17" / "scripts" / "zentao17.py").is_file() and (path / ".git").exists()


def find_source_repo(explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve() if is_source_repo(path) else None
    candidates = []
    if os.environ.get("ZENTAO17_SKILLS_REPO"):
        candidates.append(Path(os.environ["ZENTAO17_SKILLS_REPO"]).expanduser())
    if SOURCE_MARK.is_file():
        candidates.append(Path(SOURCE_MARK.read_text(encoding="utf-8").strip()))
    candidates.append(SKILL_DIR.parent.parent)  # 部署副本本身就是源仓库里的 skills/zentao17
    for path in candidates:
        if is_source_repo(path):
            return path.resolve()
    # 兜底：在用户主目录下浅层扫描（跳过隐藏目录与大目录，限时 5 秒）
    home, deadline = Path.home(), time.time() + 5
    for root, dirs, _ in os.walk(home):
        if time.time() > deadline:
            break
        if is_source_repo(root):
            return Path(root).resolve()
        depth = len(Path(root).relative_to(home).parts)
        dirs[:] = [] if depth >= 4 else [d for d in dirs if not d.startswith(".") and d not in SCAN_PRUNE]
    return None


def mirror(src, dst, top=False):
    """让 dst 与 src 内容一致（KEEP_FILES 只在顶层保护，LOCAL.md 仅在 dst 缺失时带入）；返回是否有改动。"""
    changed = False
    dst.mkdir(exist_ok=True)
    src_names = {n for n in os.listdir(src) if n not in IGNORED}
    for name in os.listdir(dst):
        if name in src_names or name in IGNORED or (top and name in KEEP_FILES):
            continue
        target = dst / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        changed = True
    for name in sorted(src_names):
        s, d = src / name, dst / name
        if top and name in KEEP_FILES and d.exists():
            continue
        if s.is_dir():
            changed = mirror(s, d) or changed
        elif not d.is_file() or s.read_bytes() != d.read_bytes():
            shutil.copy2(s, d)
            changed = True
    return changed


def deployed_version():
    m = re.search(r'^VERSION = "([^"]+)"', (SKILL_DIR / "scripts" / "zentao17.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def upstream_tree(repo, dest):
    """把源仓库已推送版本（上游跟踪分支）的 skills/zentao17 导出到 dest；返回导出目录，失败返回 None。"""
    rc, ref, _ = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ref = ref if rc == 0 and ref else "origin/main"
    r = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref, "skills/zentao17"], capture_output=True)
    if r.returncode != 0:
        return None, ref
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)
    return Path(dest) / "skills" / "zentao17", ref


def cmd_self_update(args):
    if not args.force and CHECK_STAMP.is_file() and time.time() - CHECK_STAMP.stat().st_mtime < UPDATE_INTERVAL:
        print(f"zentao17 {VERSION}：{UPDATE_INTERVAL // 3600} 小时内已检查过，跳过（--force 立即检查）")
        return
    try:
        repo = find_source_repo(args.repo)
        if repo is None:
            die("找不到 skills 源仓库（含 skills/zentao17 的 git 克隆）。"
                "用 zentao17 self-update --repo <克隆路径> 指定一次，之后自动记住", 2)
        SOURCE_MARK.write_text(f"{repo}\n", encoding="utf-8")
        head_before = git(repo, "rev-parse", "HEAD")[1]
        rc, out, err = git(repo, "fetch", "-q", "origin")
        if rc != 0:
            print(f"注意: git fetch 失败，按本机已知的远端版本同步：{err or out}")
        git(repo, "pull", "--ff-only", "-q")  # 顺带让源仓库跟上远端；失败不影响部署
        src = repo / "skills" / "zentao17"
        if src.resolve() == SKILL_DIR:
            changed = git(repo, "rev-parse", "HEAD")[1] != head_before
        else:
            # 只部署已推送到远端的内容：本机提交但未推送、或未提交的改动都不生效
            with tempfile.TemporaryDirectory() as tmp:
                tree, ref = upstream_tree(repo, tmp)
                if tree is None or not (tree / "SKILL.md").is_file():
                    die(f"无法从源仓库的 {ref} 导出 skills/zentao17", 2)
                if not (SKILL_DIR / "LOCAL.md").exists() and (src / "LOCAL.md").is_file():
                    shutil.copy2(src / "LOCAL.md", tree / "LOCAL.md")
                changed = mirror(tree, SKILL_DIR, top=True)
            unpushed = git(repo, "log", "--oneline", f"{ref}..HEAD", "--", "skills/zentao17")[1]
            if unpushed or git(repo, "status", "--porcelain", "--", "skills/zentao17")[1]:
                print(f"注意: 源仓库有未推送或未提交的 zentao17 改动，未部署（只部署已推送到 {ref} 的内容）")
    except FileNotFoundError:
        die("找不到 git 命令，无法自更新", 2)
    if rc == 0:
        CHECK_STAMP.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
    new_version = deployed_version()
    if new_version != VERSION:
        print(f"zentao17 已更新：{VERSION} → {new_version}。请重新读取 SKILL.md 与 LOCAL.md 后再继续")
    elif changed:
        print(f"zentao17 内容已更新（版本 {VERSION}）。请重新读取 SKILL.md 与 LOCAL.md 后再继续")
    else:
        print(f"zentao17 {VERSION}：已是最新")


def main():
    parser = argparse.ArgumentParser(prog="zentao17", description="禅道 17.x 命令行工具(v1 RESTful API)，拉取/解决/关闭 Bug",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--insecure", action="store_true", help="忽略 HTTPS 自签名证书校验")
    parser.add_argument("--version", action="version", version=f"zentao17 {VERSION}",
                        help="CLI 版本；与 SKILL.md 记载不一致时先重读 SKILL.md")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="配置服务地址与账号（凭证仅存本机）")
    p.add_argument("-s", "--url", help="禅道地址，如 http://IP:端口/zentao")
    p.add_argument("-u", "--account", help="账号")
    p.add_argument("-p", "--password", help="密码（建议交互输入，避免留在 shell 历史里）")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("whoami", help="当前操作账号（工号＋姓名）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("users", help="用户列表（查账号/工号）")
    p.add_argument("--search", help="按账号或姓名搜索")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_users)

    p = sub.add_parser("products", help="产品列表")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_products)

    p = sub.add_parser("projects", help="项目(课题)列表")
    p.add_argument("--search", help="按名称或编号(课题号)搜索")
    p.add_argument("--status", help="筛选: all/undone/waiting/doing/closed")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--all", action="store_true", help="取全部后再搜索/展示")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("executions", help="执行/迭代列表")
    p.add_argument("--project", type=int, help="项目 ID，查该项目下的执行")
    p.add_argument("--status", help="筛选: all/undone/waiting/doing/closed")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_executions)

    p = sub.add_parser("bugs", help="Bug 列表（状态/关键词/指派筛选在客户端执行）")
    p.add_argument("--product", type=int, help="产品 ID（推荐：测试员提单入口，未挂执行的新单也在）")
    p.add_argument("--execution", type=int, help="执行 ID（只含已挂执行的单）")
    p.add_argument("--project", type=int, help="项目(课题) ID，取整个项目的 Bug")
    p.add_argument("--status", help="筛选: all/unclosed/active/confirmed/resolved/closed/assignedtome/openedbyme/resolvedbyme")
    p.add_argument("--keyword", action="append", help="标题关键词（可多次给出，任一命中即保留）")
    p.add_argument("--assigned", help="指派给某账号（me＝当前账号）")
    p.add_argument("--order", default="id_desc", help="排序，默认 id_desc")
    p.add_argument("--limit", type=int, default=20, help="每页条数，默认 20（--all 时按 100 取）")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--all", action="store_true", help="自动翻页获取全部")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bugs)

    p = sub.add_parser("bug", help="Bug 详情（步骤＋截图清单＋actions 全历史）")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true", help="原始 JSON")
    p.set_defaults(func=cmd_bug)

    p = sub.add_parser("download", help="下载 Bug 步骤截图与附件")
    p.add_argument("id", type=int)
    p.add_argument("--dir", help="保存目录，默认 $TMPDIR/zentao17-files/<id>")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("changes", help="巡检预检：产品未关闭单与跟踪单相对上次快照的变化")
    p.add_argument("--product", type=int, required=True)
    p.add_argument("--state", required=True, help="快照文件路径（JSON）")
    p.add_argument("--keyword", action="append", help="新出现单子的标题关键词标注（不过滤）")
    p.add_argument("--track", help="追加跟踪的 Bug 编号（逗号分隔）")
    p.add_argument("--untrack", help="取消跟踪的 Bug 编号（逗号分隔）")
    p.add_argument("--set-tracked", dest="set_tracked", help="整体替换跟踪集合（逗号分隔，空串＝清空）")
    p.add_argument("--own-prefix", dest="own_prefix",
                   help="本方动作的备注前缀（如「【胡义凯·自动代理」）：actor 为本人且备注以此开头的动作不算变化")
    p.add_argument("--update", action="store_true", help="比对后把当前状态写成新快照（处理完本轮后执行）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_changes)

    p = sub.add_parser("resolve", help="解决 Bug")
    p.add_argument("id", type=int)
    p.add_argument("-r", "--resolution", default="fixed", choices=RESOLUTIONS, help="解决方案，默认 fixed")
    p.add_argument("-b", "--build", default="trunk", help="解决版本，默认 trunk")
    p.add_argument("-c", "--comment", help="备注，如修复说明/commit")
    p.add_argument("--assign", help="解决后指派给（默认指回创建者）")
    p.add_argument("--duplicate-bug", type=int, dest="duplicate_bug", help="resolution=duplicate 时的重复 Bug ID")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("close", help="关闭 Bug")
    p.add_argument("id", type=int)
    p.add_argument("-c", "--comment", help="备注")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("activate", help="激活 Bug（网页会话通道；默认指派给上次解决人）")
    p.add_argument("id", type=int)
    p.add_argument("-c", "--comment", help="备注")
    p.add_argument("--assign", help="激活后指派给")
    p.add_argument("--build", help="影响版本，默认沿用表单原值")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("assign", help="转派 Bug（网页会话通道，保留原状态）")
    p.add_argument("id", type=int)
    p.add_argument("--to", required=True, help="目标账号（工号，如 10267114）")
    p.add_argument("-c", "--comment", help="转派备注")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("comment", help="给 Bug 添加备注（网页会话通道，凭证复用 login）")
    p.add_argument("id", type=int)
    p.add_argument("-c", "--comment", required=True, help="备注内容")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("self-update", help="从 skills 源仓库同步已推送到远端的版本到部署副本（6 小时节流）")
    p.add_argument("--force", action="store_true", help="忽略节流，立即检查")
    p.add_argument("--repo", help="skills 源仓库路径（首次找不到时指定，之后自动记住）")
    p.set_defaults(func=cmd_self_update)

    args = parser.parse_args()
    if hasattr(signal, "SIGPIPE"):  # Windows Python 无 SIGPIPE，原样调用会 AttributeError 崩溃
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # 输出接 head 等管道被提前关闭时安静退出
    if args.cmd != "login" and not hasattr(args, "insecure"):
        args.insecure = False
    try:
        args.func(args)
    except NetworkError as e:
        die(f"无法连接禅道服务（读请求已自动重试 {GET_RETRIES} 次）: {e}\n请检查地址是否正确、网络是否可达")
    except ApiError as e:
        die(str(e))


if __name__ == "__main__":
    main()
