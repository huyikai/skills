#!/usr/bin/env python3
"""quota-watch — 订阅制 AI API 额度巡检

查询各家 AI 开放平台的订阅额度/余额，按「周窗口理想线性消耗曲线」判定当前用量
偏快还是偏慢，可选邮件（HTML 周报卡片）与 macOS 通知推送。

用法:
    python3 quota_check.py [--config PATH] [--dry-run] [--json]

配置查找顺序: --config 指定路径 > ./quota-watch.json > <技能目录>/config.json > ~/.config/quota-watch/config.json
支持平台: zhipu(智谱GLM Coding Plan) / minimax(Token Plan) / deepseek(按量余额)
          / relay_quota(兼容 /v1/usage 配额窗口的中转站)
"""

import argparse
import json
import os
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CST = timedelta(hours=8)
WEEK_MS = 7 * 86400 * 1000
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATHS = ["./quota-watch.json",
                        os.path.join(os.path.dirname(SCRIPT_DIR), "config.json"),
                        os.path.expanduser("~/.config/quota-watch/config.json")]


# ---------------------------------------------------------------- 基础工具

def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone(CST)).strftime("%m-%d %H:%M")


def fmt_dur(hours):
    d, h = int(hours // 24), int(round(hours % 24))
    if h == 24:
        d, h = d + 1, 0
    return f"{d}d{h}h"


def resolve(value):
    """支持 "env:VARNAME" 形式的密钥引用。"""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def http_get_json(url, headers, timeout=20):
    cmd = ["curl", "-sm", str(timeout), url]
    for h in headers:
        cmd += ["-H", h]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl 失败: {r.stderr.strip()[:120]}")
    return json.loads(r.stdout)


def verdict_cn(diff):
    if diff > 15:
        return "明显偏快", "🔥", "#dc2626"
    if diff > 5:
        return "偏快", "⚡", "#ea580c"
    if diff < -15:
        return "明显偏慢", "🐌", "#64748b"
    if diff < -5:
        return "偏慢", "🐢", "#2563eb"
    return "正常", "✅", "#16a34a"


def make_result(title, subtitle, used_pct, ideal_pct, w_start, w_end, rows, extra_note=None):
    """构建 plan 类结果：含理想曲线对比、耗尽外推。"""
    diff = used_pct - ideal_pct
    v, e, color = verdict_cn(diff)
    now_ms = time.time() * 1000
    r = {
        "ok": True, "kind": "plan", "title": title, "subtitle": subtitle,
        "used_pct": round(used_pct, 1), "ideal_pct": round(ideal_pct, 1),
        "diff": round(diff, 1), "verdict": v, "emoji": e, "color": color,
        "bar": {"used_pct": round(used_pct, 1), "ideal_pct": round(ideal_pct, 1), "color": color},
        "rows": rows,
        "window_end": w_end, "warn": None, "steady": True, "subject_seg": "",
    }
    if used_pct > 0:
        exhaust = w_start + (now_ms - w_start) / (used_pct / 100)
        if exhaust < w_end:
            r["warn"] = (f"⚠ 照当前速度约 {fmt_ms(exhaust)} 耗尽，"
                         f"早于窗口结束 {(w_end - exhaust) / 86400000:.1f} 天")
            r["steady"] = False
    r["subject_seg"] = (f"{title} {used_pct:.0f}%【{e}{'正常' if v == '正常' else v + f'{abs(diff):.0f}%'}】")
    if extra_note:
        r["rows"].append(("动态", extra_note))
    return r


def make_simple(title, subject_seg, rows, color="#64748b", emoji="ℹ️"):
    """balance/windows 类结果：无曲线判定。"""
    return {"ok": True, "kind": "simple", "title": title, "subtitle": "",
            "rows": rows, "bar": None, "warn": None, "steady": None,
            "color": color, "emoji": emoji, "diff": 0, "subject_seg": subject_seg}


def make_error(title, error):
    return {"ok": False, "kind": "simple", "title": title, "error": error,
            "rows": [], "subject_seg": f"{title}❌错误"}


# ---------------------------------------------------------------- 平台实现

def check_zhipu(p):
    key = resolve(p.get("api_key", ""))
    if not key:
        return make_error(p.get("name", "智谱 GLM"), "未配置 api_key")
    data = http_get_json("https://open.bigmodel.cn/api/monitor/usage/quota/limit",
                         [f"Authorization: {key}", "Content-Type: application/json"])
    if not data.get("success"):
        return make_error(p.get("name", "智谱 GLM"), f"接口返回异常: {json.dumps(data, ensure_ascii=False)[:150]}")
    result, extra = None, ""
    for lim in data["data"].get("limits", []):
        total, used = lim.get("usage") or 0, lim.get("currentValue") or 0
        if lim.get("unit") == 6:  # 周窗口
            end, start = lim["nextResetTime"], lim["nextResetTime"] - WEEK_MS
            ideal = (time.time() * 1000 - start) / WEEK_MS * 100
            rows = [("本周已用", f"{used / total * 100:.1f}%　用量 {used:,} / {total:,}"),
                    ("理想应达", f"{ideal:.1f}%"),
                    ("周窗口", "滚动 7 天")]
            result = make_result(p.get("name", "智谱 GLM"),
                                 f"{data['data'].get('level', '?')} 套餐",
                                 used / total * 100 if total else 0.0, ideal,
                                 start, end, rows)
        elif lim.get("unit") == 3:
            extra = (f"5小时窗口已用 {used / total * 100 if total else 0:.1f}%"
                     f"（{used:,}/{total:,}），{fmt_ms(lim['nextResetTime'])} 重置")
    if result is None:
        return make_error(p.get("name", "智谱 GLM"), "响应中无周额度数据")
    if extra:
        result["rows"].append(("动态", extra))
    return result


def check_minimax(p):
    key = resolve(p.get("api_key", ""))
    if not key:
        return make_error(p.get("name", "MiniMax"), "未配置 api_key")
    data = http_get_json("https://api.minimaxi.com/v1/token_plan/remains",
                         [f"Authorization: Bearer {key}", "Content-Type: application/json"])
    if data.get("base_resp", {}).get("status_code") != 0:
        return make_error(p.get("name", "MiniMax"), f"接口返回异常: {json.dumps(data, ensure_ascii=False)[:150]}")
    result, extras = None, []
    for m in data.get("model_remains", []):
        name = m.get("model_name", "?")
        if name == "video":
            extras.append(f"video：今日 {m.get('current_interval_usage_count', 0)}"
                          f"/{m.get('current_interval_total_count', 0)} 次 · "
                          f"本周 {m.get('current_weekly_usage_count', 0)}"
                          f"/{m.get('current_weekly_total_count', 0)} 次")
            continue
        wk_rem = m.get("current_weekly_remaining_percent")
        w_start, w_end = m.get("weekly_start_time"), m.get("weekly_end_time")
        if wk_rem is None or not w_start or not w_end:
            continue
        used_pct = 100 - wk_rem
        ideal = (time.time() * 1000 - w_start) / (w_end - w_start) * 100
        rows = [("本周已用", f"{used_pct:.1f}%　剩余 {wk_rem}%"),
                ("理想应达", f"{ideal:.1f}%"),
                ("周窗口", f"{fmt_ms(w_start)} → {fmt_ms(w_end)}")]
        result = make_result(f"{p.get('name', 'MiniMax')} {name}", "Token Plan",
                             used_pct, ideal, w_start, w_end, rows)
        result["rows"].append(("动态", f"5小时窗口剩余 {m.get('current_interval_remaining_percent')}%"))
    if extras and result:
        result["rows"].append(("动态", "；".join(extras)))
    return result if result else make_error(p.get("name", "MiniMax"), "响应中无周额度数据")


def check_deepseek(p):
    key = resolve(p.get("api_key", ""))
    if not key:
        return make_error(p.get("name", "DeepSeek"), "未配置 api_key")
    data = http_get_json("https://api.deepseek.com/user/balance",
                         [f"Authorization: Bearer {key}", "Accept: application/json"])
    if "balance_infos" not in data:
        return make_error(p.get("name", "DeepSeek"), f"接口返回异常: {json.dumps(data, ensure_ascii=False)[:150]}")
    b = data["balance_infos"][0]
    status = "✅ 可用" if data.get("is_available") else "⛔ 不可用"
    return make_simple(p.get("name", "DeepSeek"),
                       f"{p.get('name', 'DeepSeek')} {b['currency']} {b['total_balance']}",
                       [("余额", f"{b['currency']} {b['total_balance']}"),
                        ("状态", status),
                        ("赠送金", b.get("granted_balance", "?"))],
                       emoji="💰")


def check_relay_quota(p):
    key = resolve(p.get("api_key", ""))
    base = p.get("base_url", "").rstrip("/")
    if not key or not base:
        return make_error(p.get("name", "Relay"), "需配置 base_url 与 api_key")
    data = http_get_json(f"{base}/v1/usage", [f"Authorization: Bearer {key}"])
    if not data.get("isValid"):
        return make_error(p.get("name", "Relay"), "密钥无效或已过期")
    windows = {w["window"]: w for w in data.get("rate_limits", [])}
    rows = []
    seg = f"{p.get('name', 'Relay')}"
    for wname, total_days in (("5h", 5 / 24), ("1d", 1), ("7d", 7)):
        w = windows.get(wname)
        if not w:
            continue
        reset = f"，{w['reset_at'][5:16].replace('T', ' ')} 重置" if w.get("reset_at") else ""
        rows.append((f"{wname}额度", f"剩 {w['remaining']:.1f} / {w['limit']}"
                     f"（已用 {w['used']:.1f}）{reset}"))
        if wname == "7d":
            seg += f" 剩{w['remaining']:.0f}/{w['limit']:.0f}"
    t = data.get("usage", {}).get("total", {})
    if t:
        rows.append(("累计消费", f"${t.get('actual_cost', 0):,.2f}（{t.get('requests', 0):,} 次请求）"))
    r = make_simple(p.get("name", "Relay"), seg, rows, emoji="🛰")
    # 有 7d 窗口时补一条理想曲线判定
    w7 = windows.get("7d")
    if w7 and w7.get("window_start"):
        start = datetime.fromisoformat(w7["window_start"]).timestamp() * 1000
        ideal = (time.time() * 1000 - start) / (7 * 86400000) * 100
        used_pct = w7["used"] / w7["limit"] * 100 if w7["limit"] else 0
        v, e, color = verdict_cn(used_pct - ideal)
        r["rows"].append(("周进度判定", f"{e} {v}（已用 {used_pct:.1f}% vs 理想 {ideal:.1f}%）"))
        r["color"] = color
    return r


CHECKERS = {"zhipu": check_zhipu, "minimax": check_minimax,
            "deepseek": check_deepseek, "relay_quota": check_relay_quota}


# ---------------------------------------------------------------- 渲染与推送

def render_bar(bar):
    cells, fill = "", max(0, min(20, round(bar["used_pct"] / 5)))
    for i in range(20):
        bg = bar["color"] if i < fill else "#e9edf3"
        style = "height:10px;width:5%;"
        if i == 0:
            style += "border-radius:5px 0 0 5px;"
        if i == 19:
            style += "border-radius:0 5px 5px 0;"
        cells += f'<td bgcolor="{bg}" style="{style}background:{bg};"></td>'
    marker = (f'<div style="height:16px;"><div style="margin-left:{bar["ideal_pct"]}%;'
              f'font-size:11px;color:#6b7280;white-space:nowrap;">▲ 理想 {bar["ideal_pct"]}%</div></div>')
    return (f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:12px 0 2px;">'
            f'<tr>{cells}</tr></table>{marker}')


def render_card(r):
    if not r.get("ok"):
        return (f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;'
                f'padding:16px 18px;margin-bottom:14px;font-size:13px;color:#b91c1c;">'
                f'❌ {r["title"]}：{r.get("error", "查询失败")}</div>')
    head = f'''<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size:15px;font-weight:600;color:#111827;padding-bottom:2px;">{r["title"]}{f'<span style="color:#8a94a6;font-weight:400;font-size:12px;">　{r["subtitle"]}</span>' if r.get("subtitle") else ""}</td>
{f'<td align="right"><span style="background:{r["color"]};color:#fff;border-radius:10px;padding:3px 12px;font-size:12px;">{r["emoji"]} {r["verdict"]}</span></td>' if r.get("verdict") else ""}</tr></table>'''
    bar = render_bar(r["bar"]) if r.get("bar") else ""
    body = "".join(
        f'<tr><td style="color:#8a94a6;font-size:12px;width:96px;padding:3px 0;vertical-align:top;">{k}</td>'
        f'<td style="font-size:13px;color:#374151;padding:3px 0;vertical-align:top;">{v}</td></tr>'
        for k, v in r["rows"])
    steady = ""
    if r.get("steady") is True:
        steady = ('<div style="background:#f0fdf4;border-left:3px solid #16a34a;padding:8px 12px;'
                  'margin-top:10px;font-size:12px;color:#15803d;border-radius:0 6px 6px 0;">'
                  '照当前速度可平稳用满全周</div>')
    warn = (f'<div style="background:#fef2f2;border-left:3px solid #dc2626;padding:8px 12px;'
            f'margin-top:10px;font-size:12px;color:#b91c1c;border-radius:0 6px 6px 0;">{r["warn"]}</div>'
            if r.get("warn") else "")
    return (f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;'
            f'padding:16px 18px;margin-bottom:14px;">{head}{bar}'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;">{body}</table>'
            f'{steady}{warn}</div>')


def build_html(results, now_dt):
    cards = "".join(render_card(r) for r in results)
    return f'''<html><body style="margin:0;padding:24px;background:#f5f6f8;
font-family:-apple-system,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;">
<div style="max-width:520px;margin:0 auto;">
<div style="margin-bottom:14px;"><span style="font-size:18px;font-weight:700;color:#111827;">额度巡检周报</span>
<span style="font-size:13px;color:#8a94a6;margin-left:8px;">{now_dt}</span></div>
<div style="font-size:12px;color:#8a94a6;margin-bottom:12px;">判定基准：按剩余时间线性均摊；偏差 &gt; +5pp 偏快，&lt; −5pp 偏慢</div>
{cards}
<div style="font-size:11px;color:#c0c6d1;text-align:center;margin-top:4px;">由 quota-watch 自动生成</div>
</div></body></html>'''


def send_email(cfg, subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"] = subject, cfg["user"]
    msg["To"] = ", ".join(cfg["to"]) if isinstance(cfg["to"], list) else cfg["to"]
    msg.attach(MIMEText(html, "html", "utf-8"))
    port = int(cfg.get("port", 465))
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["host"], port, timeout=25)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=25)
        server.starttls()
    try:
        server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["user"], cfg["to"], msg.as_string())
    finally:
        server.quit()
    return f"📧 已推送邮件至 {msg['To']}"


def notify_macos(title, text, urgent):
    text = text.replace('"', "'")
    script = f'display notification "{text}" with title "{title}"'
    if urgent:
        script += ' sound name "Ping"'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return "🔔 已发本地通知" if r.returncode == 0 else f"🔔 本地通知失败: {r.stderr.strip()[:80]}"


# ---------------------------------------------------------------- 主流程

def load_config(path=None):
    candidates = [path] + DEFAULT_CONFIG_PATHS if path else DEFAULT_CONFIG_PATHS
    for c in candidates:
        if c and os.path.isfile(os.path.expanduser(c)):
            with open(os.path.expanduser(c)) as f:
                return json.load(f), os.path.expanduser(c)
    raise SystemExit("未找到配置文件。请参考 config.example.json 创建 ./quota-watch.json "
                     "或 ~/.config/quota-watch/config.json")


def main():
    ap = argparse.ArgumentParser(description="quota-watch 额度巡检")
    ap.add_argument("--config", help="配置文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只输出报告，不发送邮件/通知")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    cfg, cfg_path = load_config(args.config)
    now = time.time()
    now_dt = datetime.fromtimestamp(now, timezone(CST)).strftime("%Y-%m-%d %H:%M")

    results = []
    for p in cfg.get("providers", []):
        checker = CHECKERS.get(p.get("type"))
        if not checker:
            results.append(make_error(p.get("name", p["type"]), f"未知类型 {p['type']}"))
            continue
        try:
            results.append(checker(p))
        except Exception as e:
            results.append(make_error(p.get("name", p["type"]), str(e)[:150]))

    # 历史记录（小时增量）；读写失败只降级为提示，不阻断报告与推送
    history_path = os.path.expanduser(cfg.get("history_path", "~/.quota-watch/history.jsonl"))
    prev = None
    try:
        with open(history_path) as f:
            lines = f.read().strip().splitlines()
            if lines:
                prev = json.loads(lines[-1])
    except Exception:
        prev = None
    hist_err = None
    rec = {"ts": now}
    for r in results:
        if r.get("ok") and r.get("used_pct") is not None:
            rec[r["title"]] = r["used_pct"]
            if prev and r["title"] in prev:
                h = (now - prev["ts"]) / 3600
                r["rows"].append(("小时增量", f"{r['used_pct'] - prev[r['title']]:+.1f}pp（{h:.1f}h 前）"))
    try:
        if os.path.dirname(history_path):
            os.makedirs(os.path.dirname(history_path), exist_ok=True)
        with open(history_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        hist_err = f"⚠ 历史记录写入失败（不影响本次报告与推送）: {e}"

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # 纯文本输出
    out = [f"额度巡检 @ {now_dt}（配置: {cfg_path}）", ""]
    if hist_err:
        out += [hist_err, ""]
    for r in results:
        if not r.get("ok"):
            out.append(f"❌ {r['title']}: {r.get('error')}")
        else:
            out.append(f"【{r['title']}】" + (f"{r['emoji']} {r['verdict']}" if r.get("verdict") else ""))
            out += [f"  {k}: " + v.replace("　", "  ") for k, v in r["rows"]]
            if r.get("warn"):
                out.append(f"  {r['warn']}")
            elif r.get("steady") is True:
                out.append("  照当前速度可平稳用满全周")
        out.append("")

    status_msgs = []
    urgent = any(r.get("warn") or (r.get("diff", 0) or 0) > 5 or not r.get("ok") for r in results)
    subject = "[quota-watch] " + " ".join(r["subject_seg"] for r in results if r.get("ok")) + f" {now_dt.split(' ')[1]}"
    if not args.dry_run:
        email_cfg = cfg.get("email", {})
        if email_cfg.get("enabled"):
            try:
                status_msgs.append(send_email(email_cfg, subject, build_html(results, now_dt)))
            except Exception as e:
                status_msgs.append(f"📧 邮件推送失败: {e}")
        if cfg.get("mac_notify"):
            status_msgs.append(notify_macos(f"额度巡检 {now_dt.split(' ')[1]}",
                                            " ".join(r["subject_seg"] for r in results if r.get("ok")),
                                            urgent))
    out += status_msgs
    print("\n".join(out))


if __name__ == "__main__":
    main()
