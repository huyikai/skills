#!/usr/bin/env python3
"""zentao17 — 自部署禅道 17.x 命令行工具（基于 v1 RESTful API，兼容禅道 12.0+）

用法示例:
  zentao17 login                          # 交互式配置服务地址与账号
  zentao17 products                       # 查看产品列表（获取产品 ID）
  zentao17 bugs --product 1               # 某产品的 Bug 列表
  zentao17 bugs --product 1 --status unclosed --limit 50
  zentao17 bug 123                        # Bug 详情
  zentao17 resolve 123 --comment "已在 commit abc123 修复"
  zentao17 close 123 --comment "验证通过"
  zentao17 activate 123
  zentao17 assign 123 --to 10267114 -c "转产品拍板"   # 转派（网页会话通道）
  zentao17 comment 123 -c "进展更新..."               # 独立备注（网页会话通道）

凭证来源优先级: 命令行参数 > 环境变量(ZENTAO_URL/ZENTAO_ACCOUNT/ZENTAO_PASSWORD) > ~/.config/zentao17/credentials.json
"""
import argparse
import getpass
import json
import os
import re
import ssl
import sys
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


def http(method, base, path, body=None, token=None, insecure=False):
    url = base + API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Token", token)
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        die(f"无法连接禅道服务({url}): {reason}\n请检查地址是否正确、网络是否可达")


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
        die(f"接口不存在(HTTP 404): {path}\n请确认禅道版本支持 v1 API(>= 12.0)")
    if status == 403:
        die("无权限(HTTP 403): 当前账号对目标对象/产品没有操作权限")
    if status >= 400:
        msg = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        die(f"请求失败(HTTP {status}): {msg}")
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


def strip_html(html):
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").strip()


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


def cmd_bugs(args):
    params = {"order": args.order, "limit": args.limit, "page": args.page}
    if args.status:
        params["status"] = args.status
    if args.execution:
        path = f"/executions/{args.execution}/bugs"
    elif args.project:
        path = f"/projects/{args.project}/bugs"
    elif args.product:
        path = "/bugs"
    else:
        die("必须指定范围: --execution <执行ID> / --project <项目ID> / --product <产品ID>")
    if args.product:
        params["product"] = args.product
    data = api("GET", path, params=params, insecure=args.insecure)
    if args.all:  # 自动翻页取全部
        all_bugs = data.get("bugs", []) if isinstance(data, dict) else []
        total = data.get("total", len(all_bugs)) if isinstance(data, dict) else len(all_bugs)
        page = args.page
        while all_bugs and len(all_bugs) < int(total or 0) and page < 100:
            page += 1
            params["page"] = page
            more = api("GET", path, params=params, insecure=args.insecure)
            batch = more.get("bugs", []) if isinstance(more, dict) else []
            if not batch:
                break
            all_bugs.extend(batch)
        data = {"page": 1, "total": len(all_bugs), "limit": len(all_bugs), "bugs": all_bugs}
    bugs = data.get("bugs", []) if isinstance(data, dict) else []
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"共 {data.get('total', len(bugs))} 条（第 {data.get('page', args.page)} 页，每页 {data.get('limit', args.limit)} 条）")
    rows = [{"ID": b.get("id"), "状态": b.get("statusName") or b.get("status"), "严重": b.get("severity"),
             "优先": b.get("pri"), "指派给": b.get("assignedTo"), "创建人": b.get("openedBy"), "标题": b.get("title")}
            for b in bugs]
    table(rows, ["ID", "状态", "严重", "优先", "指派给", "创建人", "标题"])


def cmd_bug(args):
    data = api("GET", f"/bugs/{args.id}", insecure=args.insecure)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    bug = data.get("bug", data) if isinstance(data, dict) else {}
    keys = ["id", "title", "severity", "pri", "status", "statusName", "assignedTo", "openedBy", "openedDate",
            "resolvedBy", "resolvedDate", "resolution", "closedBy", "closedDate", "type", "steps"]
    for k in keys:
        if k not in bug:
            continue
        v = bug[k]
        if k == "steps":
            v = strip_html(v)
        if v not in (None, ""):
            print(f"{k:>14}: {v}")


def cmd_resolve(args):
    if args.resolution not in RESOLUTIONS:
        die(f"无效的解决方案: {args.resolution}，可选: {', '.join(RESOLUTIONS)}")
    body = {"resolution": args.resolution, "resolvedBuild": args.build, "comment": args.comment or ""}
    if args.assign:
        body["assignedTo"] = args.assign
    if args.resolution == "duplicate" and args.duplicate_bug:
        body["duplicateBug"] = args.duplicate_bug
    api("POST", f"/bugs/{args.id}/resolve", body=body, insecure=args.insecure)
    print(f"Bug #{args.id} 已解决（{RESOLUTION_NAMES[args.resolution]}）" + (f"，备注: {args.comment}" if args.comment else ""))


def cmd_close(args):
    api("POST", f"/bugs/{args.id}/close", body={"comment": args.comment or ""}, insecure=args.insecure)
    print(f"Bug #{args.id} 已关闭" + (f"，备注: {args.comment}" if args.comment else ""))


def cmd_activate(args):
    body = {"comment": args.comment or ""}
    if args.assign:
        body["assignedTo"] = args.assign
    if args.build:
        body["openedBuild"] = args.build
    api("POST", f"/bugs/{args.id}/activate", body=body, insecure=args.insecure)
    print(f"Bug #{args.id} 已激活")


# ---------------------------------------------------------------------------
# 网页会话通道（assign / comment）
# v1 API 在禅道 17.8 上不开放独立备注与转派（PUT /bugs/{id} 忽略 assignedTo/comment，
# activate 路由 404），但网页路由 bug-assign-{id}.html 与 bug-view 页的备注表单支持——
# 走 user-login 换 zentaosid 会话。凭证复用 login 已存的本地配置，不新增交互。
WEB_COOKIE_FILE = CONFIG_DIR / "web-session.json"


class WebClient:
    """带 Cookie 的网页请求客户端（不自动跟随重定向，便于识别登录跳转）。"""

    def __init__(self, base, insecure=False):
        self.base = base
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if insecure:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, path, data=None, referer=None, xhr=False):
        url = self.base + path
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        # 禅道 17 反 CSRF：无 Referer 的登录后请求会被渲染成访客首页。
        req.add_header("Referer", self.base + (referer if referer else "/"))
        if xhr:
            req.add_header("X-Requested-With", "XMLHttpRequest")
        req.add_header("User-Agent", "Mozilla/5.0 zentao17-cli/1.1")
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, url, e.read().decode("utf-8", "replace")

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
        except Exception:
            pass
    return web_login(creds, insecure)


def cmd_assign(args):
    creds = load_creds()
    client = web_client(creds, args.insecure)
    # 禅道 17 的转派路由是 bug-assignTo（bug-assign 为空响应），表单含
    # assignedTo/status/mailto[]/comment 四字段；onlybody 仅为弹窗展示参数。
    form_path = f"/bug-assignTo-{args.id}.html"
    view_path = f"/bug-view-{args.id}.html"
    status, final, body = client.request(f"{form_path}?onlybody=yes", referer=view_path)
    if status != 200 or len(body or "") < 200:
        client = web_login(creds, args.insecure)
        status, final, body = client.request(f"{form_path}?onlybody=yes", referer=view_path)
    if len(body or "") < 200:
        die(f"转派表单获取失败（HTTP {status}），请确认 Bug 编号与账号权限。")
    status, final, body = client.request(
        form_path,
        data={"assignedTo": args.to, "status": "active", "mailto[]": "", "comment": args.comment or ""},
        referer=view_path,
    )
    if status >= 400:
        die(f"转派请求失败(HTTP {status})，请确认账号有该 Bug 的编辑权限。")
    # 用 v1 API 回读复核（自验证，不凭网页响应猜）。
    data = api("GET", f"/bugs/{args.id}", insecure=args.insecure)
    bug = data.get("bug", data)
    assigned = bug.get("assignedTo")
    assigned = assigned.get("account") if isinstance(assigned, dict) else assigned
    if str(assigned) != str(args.to):
        die(f"转派未生效（当前仍指派给 {assigned}）。请人工在网页核对。")
    print(f"Bug #{args.id} 已转派给 {args.to}" + (f"，备注: {args.comment}" if args.comment else ""))


def cmd_comment(args):
    creds = load_creds()
    client = web_client(creds, args.insecure)
    view_path = f"/bug-view-{args.id}.html"
    # 禅道 17 备注路由：action-comment-bug-{id}.html（bug-view 页备注表单的 action）。
    status, final, body = client.request(
        f"/action-comment-bug-{args.id}.html",
        data={"comment": args.comment},
        referer=view_path,
        xhr=True,
    )
    if status >= 400:
        die(f"备注请求失败(HTTP {status})，请确认账号有该 Bug 的查看/备注权限。")
    # 回读 actions 复核：最后一条 action 的 comment 应包含提交文本的开头。
    data = api("GET", f"/bugs/{args.id}", insecure=args.insecure)
    bug = data.get("bug", data)
    actions = bug.get("actions") or []
    head = (args.comment or "").strip()[:20]
    ok = any(head and head in (a.get("comment") or "") for a in actions[-5:])
    if not ok:
        die("备注可能未写入（actions 未检到新评论），请人工在网页核对。")
    who = actions[-1].get("actor") if actions else "?"
    print(f"Bug #{args.id} 已添加备注（by {who}）: { (args.comment or '')[:50] }...")


def main():
    parser = argparse.ArgumentParser(prog="zentao17", description="禅道 17.x 命令行工具(v1 RESTful API)，拉取/解决/关闭 Bug",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--insecure", action="store_true", help="忽略 HTTPS 自签名证书校验")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="配置服务地址与账号（凭证仅存本机）")
    p.add_argument("-s", "--url", help="禅道地址，如 http://IP:端口/zentao")
    p.add_argument("-u", "--account", help="账号")
    p.add_argument("-p", "--password", help="密码（建议交互输入，避免留在 shell 历史里）")
    p.set_defaults(func=cmd_login)

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

    p = sub.add_parser("bugs", help="Bug 列表")
    p.add_argument("--product", type=int, help="产品 ID（过滤用，或单独作范围）")
    p.add_argument("--execution", type=int, help="执行 ID（与 --project 二选一）")
    p.add_argument("--project", type=int, help="项目(课题) ID，取整个项目的 Bug")
    p.add_argument("--status", help="筛选: all/unclosed/closed/assignedtome/openedbyme/resolvedbyme/unconfirmed")
    p.add_argument("--order", default="id_desc", help="排序，默认 id_desc")
    p.add_argument("--limit", type=int, default=20, help="每页条数，默认 20")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--all", action="store_true", help="自动翻页获取全部")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bugs)

    p = sub.add_parser("bug", help="Bug 详情")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bug)

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

    p = sub.add_parser("activate", help="激活 Bug")
    p.add_argument("id", type=int)
    p.add_argument("-c", "--comment", help="备注")
    p.add_argument("--assign", help="激活后指派给")
    p.add_argument("--build", help="影响版本")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("assign", help="转派 Bug（网页会话通道，凭证复用 login）")
    p.add_argument("id", type=int)
    p.add_argument("--to", required=True, help="目标账号（工号，如 10267114）")
    p.add_argument("-c", "--comment", help="转派备注")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("comment", help="给 Bug 添加备注（网页会话通道，凭证复用 login）")
    p.add_argument("id", type=int)
    p.add_argument("-c", "--comment", required=True, help="备注内容")
    p.set_defaults(func=cmd_comment)

    args = parser.parse_args()
    if args.cmd != "login" and not hasattr(args, "insecure"):
        args.insecure = False
    args.func(args)


if __name__ == "__main__":
    main()
