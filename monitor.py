#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCWU 工程实践与创新中心「通知公告」增量监测器
目标页: https://www2.ncwu.edu.cn/gcxlzx/zxdt/tzgg.htm

核心机制:
  1. 抓列表页(第1页) -> 解析出每条公告的 文章ID / 标题 / 日期 / 链接
  2. 用「文章ID」做主键与历史 state.json 比对 (ID 单调递增, 是最稳的唯一键)
  3. 有新增 -> 推送通知; 无新增 -> 静默退出
  4. 解析到 0 条 -> 报错退出(不静默), 防止页面改版后"假的无更新"

仅依赖标准库, 直接 python monitor.py 即可运行。
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import hashlib
from datetime import datetime
from urllib import request, parse
from urllib.parse import urljoin

# ============================== 配置区 ==============================
LIST_URL = "https://www2.ncwu.edu.cn/gcxlzx/zxdt/tzgg.htm"
BASE_DIR = "https://www2.ncwu.edu.cn/gcxlzx/zxdt/"   # 用于把相对链接拼成绝对链接
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
ALERT_LOG = os.path.join(HERE, "alerts.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20      # 秒
RETRY = 3         # 失败重试次数(含首次)
RETRY_WAIT = 5    # 重试间隔(秒)

# 通知渠道: 优先从环境变量读取(GitHub Actions Secret), 命令行/本地可留空
#   SERVERCHAN_KEY  微信推送 (Server酱, https://sct.ftqq.com)  <-- 当前使用
#   PUSHPLUS_TOKEN  微信推送备选 (PushPlus, https://pushplus.plus)
#   BARK_KEY        iPhone 推送 (Bark)
#   WEBHOOK_URL     企业微信/钉钉/飞书机器人(自动识别格式)
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


NOTIFY = {
    "console": True,                              # 控制台/CI 日志
    "alert_file": os.environ.get("CI") != "true", # CI 里不写文件(日志本身就是记录)
    "bark": _env("BARK_KEY"),
    "serverchan": _env("SERVERCHAN_KEY"),
    "pushplus": _env("PUSHPLUS_TOKEN"),
    "webhook": _env("WEBHOOK_URL"),
}
# ===================================================================

ITEM_RE = re.compile(r"""<li[^>]*?onclick="location='([^']+)'"[^>]*>(.*?)</li>""", re.S)
TITLE_RE = re.compile(r"""<h[0-9][^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h[0-9]>""", re.S)
DAY_RE = re.compile(r"""<span[^>]*class="[^"]*\bday\b[^"]*"[^>]*>\s*([\d\-/\.]+)\s*</span>""")
YEAR_RE = re.compile(r"""<span[^>]*class="[^"]*\byear\b[^"]*"[^>]*>\s*(\d{4})\s*</span>""")
ID_RE = re.compile(r"""/info/\d+/(\d+)\.htm""")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url: str) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last_err = None
    for i in range(RETRY):
        try:
            req = request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            })
            with request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                raw = r.read()
                ctype = (r.headers.get("Content-Type") or "").lower()
                enc = "gb18030" if "gb" in ctype else "utf-8"
                return raw.decode(enc, errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < RETRY - 1:
                log(f"抓取失败({i+1}/{RETRY}): {type(e).__name__}: {e}, {RETRY_WAIT}s 后重试")
                time.sleep(RETRY_WAIT)
    raise RuntimeError(f"抓取失败 {url}: {last_err}")


def parse_items(html: str):
    items = []
    for m in ITEM_RE.finditer(html):
        link, body = m.group(1), m.group(2)
        tm = TITLE_RE.search(body)
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else ""
        title = re.sub(r"\s+", " ", title)
        dm, ym = DAY_RE.search(body), YEAR_RE.search(body)
        day = dm.group(1).strip() if dm else ""
        year = ym.group(1).strip() if ym else ""
        date = f"{year}-{day}" if year and day else (day or "")
        im = ID_RE.search(link)
        aid = int(im.group(1)) if im else None
        # 主键: 优先文章ID(单调递增), 退化时才用标题哈希
        key = str(aid) if aid is not None else "h:" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        items.append({
            "key": key,
            "id": aid,
            "title": title,
            "date": date,
            "url": urljoin(BASE_DIR, link),
        })
    return items


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def push_bark(key: str, title: str, body: str) -> None:
    url = f"{key.rstrip('/')}/{parse.quote(title)}/{parse.quote(body)}"
    with request.urlopen(request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")


def _serverchan_url(key: str) -> str:
    """接受完整 URL 或裸 SendKey(如 SCT437840Txxx), 统一成可请求的端点。"""
    key = key.strip()
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return f"https://sctapi.ftqq.com/{key}.send"


def push_serverchan(key: str, title: str, body: str) -> None:
    url = _serverchan_url(key)
    data = parse.urlencode({"title": title, "desp": body}).encode()
    with request.urlopen(request.Request(url, data=data, headers={"User-Agent": UA}), timeout=TIMEOUT) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if resp.get("code") not in (0, None):
        raise RuntimeError(f"ServerChan 返回异常: {resp}")
    log(f"ServerChan 已投递 (pushid={resp.get('data', {}).get('pushid')})")


def push_pushplus(token: str, title: str, body: str) -> None:
    payload = json.dumps({"token": token, "title": title, "content": body, "template": "txt"}).encode()
    req = request.Request("http://www.pushplus.plus/send", data=payload,
                          headers={"Content-Type": "application/json", "User-Agent": UA})
    with request.urlopen(req, timeout=TIMEOUT) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if resp.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回异常: {resp}")


def push_webhook(url: str, title: str, body: str) -> None:
    if "qyapi.weixin.qq.com" in url:          # 企业微信
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
    elif "dingtalk.com" in url:               # 钉钉
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
    else:                                     # 飞书 / 通用
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{body}"}}
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json", "User-Agent": UA})
    with request.urlopen(req, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")


def notify(new_items: list) -> bool:
    """推送通知。返回 True = 至少一个远程渠道成功(或未配置远程渠道)。"""
    title = f"【通知公告】发现 {len(new_items)} 条更新"
    lines = [f"[{it['date']}] {it['title']}" for it in new_items]
    body_short = "\n".join(lines)
    body_long = body_short + "\n\n详情:\n" + "\n".join(f"· {it['url']}" for it in new_items)

    if NOTIFY["console"]:
        log(title)
        log(body_long)
    if NOTIFY["alert_file"]:
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{title}\n{body_long}\n")

    channels = []
    if NOTIFY["serverchan"]:
        channels.append(("ServerChan", lambda: push_serverchan(NOTIFY["serverchan"], title, body_short)))
    if NOTIFY["pushplus"]:
        channels.append(("PushPlus", lambda: push_pushplus(NOTIFY["pushplus"], title, body_short)))
    if NOTIFY["bark"]:
        channels.append(("Bark", lambda: push_bark(NOTIFY["bark"], title, body_short)))
    if NOTIFY["webhook"]:
        channels.append(("Webhook", lambda: push_webhook(NOTIFY["webhook"], title, body_long)))
    if not channels:
        log("警告: 未配置任何远程通知渠道 —— 只在本地留了记录")
        return True

    ok = False
    for name, fn in channels:
        try:
            fn()
            log(f"✓ {name} 推送成功")
            ok = True
        except Exception as e:  # noqa: BLE001
            log(f"✗ {name} 推送失败: {type(e).__name__}: {e}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="NCWU 通知公告增量监测")
    ap.add_argument("--reset", action="store_true", help="清空历史并重建基线(不推送)")
    ap.add_argument("--show", action="store_true", help="只打印当前列表, 不读写状态")
    ap.add_argument("--push-test", action="store_true", help="发一条测试通知, 验证渠道是否连通")
    args = ap.parse_args()

    if args.push_test:
        demo = [{"date": f"{datetime.now():%Y-%m-%d}",
                 "title": "【测试】通知渠道连通性验证",
                 "url": LIST_URL}]
        ok = notify(demo)
        log("推送测试结果: " + ("成功" if ok else "失败"))
        return 0 if ok else 4

    log(f"抓取列表页: {LIST_URL}")
    try:
        html = fetch(LIST_URL)
    except Exception as e:  # noqa: BLE001
        log(f"严重错误: {e}")
        return 2

    items = parse_items(html)

    # —— 结构校验: 解析不到内容时显式失败, 避免"静默误判为无更新" ——
    if len(items) == 0:
        log("严重错误: 解析到 0 条公告 —— 页面结构可能已变化, 请检查解析规则并更新脚本")
        return 3
    if len(items) < 3:
        log(f"警告: 仅解析到 {len(items)} 条, 明显少于预期(通常 10 条), 可能是页面改版")

    if args.show:
        for it in items:
            log(f"  {it['date']:<12} id={it['id']}  {it['title'][:40]}")
        return 0

    state = load_state()
    seen = state.get("seen", {})

    if args.reset or not seen:
        now = datetime.now().isoformat(timespec="seconds")
        for it in items:
            it["first_seen"] = now
            seen[it["key"]] = it
        save_state({"last_check": now, "last_max_id": max([i["id"] or 0 for i in items]),
                    "seen": seen})
        log(f"[基线初始化] 已记录 {len(items)} 条历史, 本次不推送。之后再新增公告就会通知。")
        return 0

    new_items = [it for it in items if it["key"] not in seen]
    new_items.sort(key=lambda x: (x["id"] or 0), reverse=True)

    now = datetime.now().isoformat(timespec="seconds")
    push_ok = True
    if new_items:
        log(f"检测到 {len(new_items)} 条新公告")
        push_ok = notify(new_items)
        if not push_ok:
            log("注意: 所有远程通知渠道均推送失败, 本次退出码 4(GitHub Actions 会标红)")
        for it in new_items:
            it["first_seen"] = now
    else:
        log("无更新")

    for it in items:                       # 增量并入历史(保留首次发现时间)
        if it["key"] not in seen:
            seen[it["key"]] = it
    save_state({"last_check": now, "last_max_id": max(i["id"] or 0 for i in items), "seen": seen})
    log(f"状态已更新, 历史累计 {len(seen)} 条")
    return 0 if push_ok else 4


if __name__ == "__main__":
    sys.exit(main())
