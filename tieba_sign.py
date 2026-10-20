#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度贴吧自动签到（Python 版）
- 从 cookie.txt 读取 BDUSS（支持 Netscape cookies.txt 格式或纯文本 "BDUSS=xxx" / 直接粘贴 BDUSS 值）
- 流程：获取 tbs -> 一键签到(msign，循环重试) -> 拉取关注列表(forumGuide 分页) -> 逐吧补签(sign)
- 逐吧签到带自适应退避：连续 160002 频控时自动加倍间隔
"""

import hashlib
import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

APP_HOST = "http://c.tieba.baidu.com"  # 参考云签到项目：客户端签到用 c.tieba.baidu.com（tiebac.baidu.com 直连会超时）
WEB_HOST = "https://tieba.baidu.com"
CLIENT_VERSION = "12.64.1.1"
SIGN_SUFFIX = "tiebaclient!!!"
MAX_RN = 200
SLEEP_MS = 600
CONCURRENCY = 8         # 并发签到线程数（提速关键）
MSIGN_RETRY = 1          # msign 遇到 340011 时的重试次数
MSIGN_RETRY_WAIT = 5     # 每次重试等待秒数
COOKIE_FILE = Path(__file__).parent / "cookie.txt"

session = requests.Session()
session.trust_env = False  # 不走系统代理（代理会导致 tiebac.baidu.com SSL 握手超时）
session.headers.update({
    "User-Agent": f"bdtb for Android {CLIENT_VERSION}",
    "Accept": "application/json, text/plain, */*",
})


def load_bduss(path: Path) -> str:
    """从 cookie.txt 提取 BDUSS。支持 Netscape 格式、BDUSS=xxx、或直接是值。"""
    if not path.exists():
        sys.exit(f"未找到 {path}，请先创建并写入 BDUSS")
    text = path.read_text(encoding="utf-8")
    # Netscape 格式行
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] == "BDUSS":
            return parts[6]
    # BDUSS=xxx 形式
    m = re.search(r"BDUSS\s*=\s*([A-Za-z0-9~_%\-]+)", text)
    if m:
        return m.group(1)
    # 整个文件就是一个值
    val = text.strip()
    if val and re.fullmatch(r"[A-Za-z0-9~_%\-]{50,}", val):
        return val
    sys.exit("cookie.txt 中未找到 BDUSS")


BDUSS = load_bduss(COOKIE_FILE)
session.cookies.set("BDUSS", BDUSS, domain=".baidu.com")


def md5_sign(pairs) -> str:
    """贴吧客户端协议签名：参数按名排序拼接 + tiebaclient!!! 后取大写 MD5（参考云签到项目）。"""
    sorted_pairs = sorted(pairs, key=lambda kv: kv[0])
    body = "".join(f"{k}={v}" for k, v in sorted_pairs) + SIGN_SUFFIX
    return hashlib.md5(body.encode("utf-8")).hexdigest().upper()


def post(host: str, path: str, pairs, extra_headers=None) -> dict:
    data = dict(pairs)
    r = session.post(host + path, data=data, headers=extra_headers, timeout=15)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{path} 返回非 JSON: {r.text[:300]}")


def get_tbs() -> str:
    print("获取 tbs ...")
    r = session.get(f"{WEB_HOST}/dc/common/tbs", timeout=15)
    data = r.json()
    if data.get("is_login") != 1 or not data.get("tbs"):
        raise RuntimeError("获取 tbs 失败，BDUSS 可能无效或未登录")
    print("tbs 获取成功")
    return data["tbs"]


def multi_sign() -> int:
    """一键签到，返回本轮成功签到数。"""
    pairs = [("_client_version", CLIENT_VERSION), ("subapp_type", "hybrid")]
    data = post(WEB_HOST, "/c/c/forum/msign", pairs, {"Subapp-Type": "hybrid"})
    errno = int(data.get("error", {}).get("errno", 0) or 0)
    if errno != 0:
        raise RuntimeError(f"msign errno={errno} msg={data['error'].get('usermsg') or data['error'].get('errmsg')}")
    info = data.get("info") or []
    ok = sum(1 for f in info if str(f.get("signed")) == "1" and int(f.get("error", {}).get("err_no", 0) or 0) == 0)
    print(f"一键签到完成，本轮处理 {len(info)} 个，成功 {ok} 个")
    return ok


def fetch_unsigned_forums(tbs: str):
    """分页拉取关注列表，返回未签到贴吧 (名称, fid) 列表。"""
    unsigned = []
    page = 1
    while page < 9999:
        pairs = [
            ("tbs", tbs),
            ("sort_type", "3"),
            ("call_from", "3"),
            ("page_no", str(page)),
            ("res_num", str(MAX_RN)),
        ]
        data = post(WEB_HOST, "/c/f/forum/forumGuide", pairs, {"Subapp-Type": "hybrid"})
        if int(data.get("error_code", 0) or 0) != 0:
            raise RuntimeError(f"获取关注列表失败: {data.get('error_msg')}")
        forums = data.get("like_forum") or []
        unsigned += [(f["forum_name"], f.get("forum_id")) for f in forums if not int(f.get("is_sign", 0) or 0)]
        print(f"关注列表第 {page} 页，累计未签 {len(unsigned)} 个")
        if not data.get("like_forum_has_more"):
            break
        page += 1
        time.sleep(0.2)
    print(f"共发现未签到贴吧 {len(unsigned)} 个")
    return unsigned


def sign_one(tbs: str, fname: str, fid=None):
    """客户端签到（参考云签到项目 DoSign_Client：c.tieba.baidu.com + fid + kw）。"""
    pairs = [
        ("BDUSS", BDUSS),
        ("_client_version", CLIENT_VERSION),
        ("kw", fname),
        ("tbs", tbs),
    ]
    if fid:
        pairs.append(("fid", str(fid)))
    data = post(APP_HOST, "/c/c/forum/sign", {**dict(pairs), "sign": md5_sign(pairs)})
    err = int(data.get("error_code", 0) or 0)
    # 160002/1101/1102 均视为已签到（参考云签到项目）
    if err in (160002, 1101, 1102):
        raise RuntimeError(f"已签到({err})")
    if err != 0:
        raise RuntimeError(f"{data.get('error_msg') or err}")


def main():
    tbs = get_tbs()

    # 1) 一键签到，带重试（340011 = 签得太快）
    for attempt in range(1, MSIGN_RETRY + 1):
        try:
            multi_sign()
            break
        except RuntimeError as e:
            if "340011" in str(e) and attempt < MSIGN_RETRY:
                print(f"一键签到被限速，{MSIGN_RETRY_WAIT}s 后重试（{attempt}/{MSIGN_RETRY}）")
                time.sleep(MSIGN_RETRY_WAIT)
            else:
                print(f"一键签到跳过：{e}")
                break

    # 2) 逐吧补签（并发签到，大幅提速）
    unsigned = fetch_unsigned_forums(tbs)
    success = skipped = failed = 0
    lock = threading.Lock()
    progress = [0]

    def worker(item):
        nonlocal success, skipped, failed
        idx, (fname, fid) = item
        try:
            sign_one(tbs, fname, fid)
            with lock:
                success += 1
                progress[0] += 1
                print(f"[{progress[0]}/{len(unsigned)}] ✓ {fname}")
        except (RuntimeError, requests.RequestException) as e:
            msg = str(e)
            # 网络超时等临时错误：重试一次
            if isinstance(e, requests.RequestException):
                try:
                    time.sleep(3)
                    sign_one(tbs, fname, fid)
                    with lock:
                        success += 1
                        progress[0] += 1
                        print(f"[{progress[0]}/{len(unsigned)}] ✓ {fname}（重试成功）")
                    return
                except Exception as e2:
                    e = e2
                    msg = str(e2)
            with lock:
                progress[0] += 1
                if "已签到" in msg or "160002" in msg or "重复" in msg:
                    skipped += 1
                    print(f"[{progress[0]}/{len(unsigned)}] ⟳ 已签/频控 {fname}: {msg}")
                else:
                    failed += 1
                    print(f"[{progress[0]}/{len(unsigned)}] ✗ {fname}: {msg}")

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(worker, enumerate(unsigned, 1)))

    print(f"完成。成功 {success}，已签/跳过 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main()
