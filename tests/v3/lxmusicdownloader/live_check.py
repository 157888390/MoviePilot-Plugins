# -*- coding: utf-8 -*-
"""LX Sync Server 手动联调脚本（只读，不落盘）。

用于验证「搜索 -> 解析直链 -> 下载」全链路以及与鉴权契约的一致性。
凭据一律从环境变量读取，**不要把服务端地址、用户名、令牌写进仓库**：

    LX_HOST=https://your-lx-server \\
    LX_USER=your-user \\
    LX_TOKEN=your-token \\
    python tests/v3/lxmusicdownloader/live_check.py

判据（与 plugins.v3/lxmusicdownloader/README.md 的「鉴权契约」一致）：
- 只带 token 不带用户名时 /api/music/url 会退回公开用户并 500；
- 必须 username + token 成对，/api/music/url 才能解析成功。

不依赖 MoviePilot 运行时，也不写入任何文件。
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

HOST = (os.environ.get("LX_HOST") or "").rstrip("/")
USER = os.environ.get("LX_USER") or ""
TOKEN = os.environ.get("LX_TOKEN") or ""

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

AUTH_FULL = {"x-user-name": USER, "x-user-token": TOKEN}
AUTH_TOKEN_ONLY = {"x-user-token": TOKEN}


def call(path, headers=None, params=None, method="GET", body=None, timeout=30):
    url = HOST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return resp.status, raw[:200]
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return err.code, raw[:200]
    except Exception as err:  # noqa: BLE001
        return -1, f"{type(err).__name__}: {err}"


def main() -> int:
    if not HOST:
        print("请设置 LX_HOST / LX_USER / LX_TOKEN 环境变量")
        return 2

    results = []

    status, info = call("/api/user/auth/verify", AUTH_FULL)
    valid = isinstance(info, dict) and info.get("valid")
    results.append(("verify (user+token)", status, valid))
    print(f"[1] verify user+token -> {status} valid={info.get('valid') if isinstance(info, dict) else info}")

    status, songs = call("/api/music/search", AUTH_FULL,
                         {"name": "稻香", "source": "kw", "type": "song", "page": 1, "pages": 1, "limit": 1})
    has_song = isinstance(songs, list) and len(songs) > 0
    results.append(("search", status, has_song))
    if not has_song:
        print("[2] search 未返回歌曲，终止")
        return 1
    song = songs[0]
    print(f"[2] search -> {status} {song.get('name')} - {song.get('singer')} 音质={[t.get('type') for t in song.get('types', [])]}")

    status, full = call("/api/music/url", AUTH_FULL, None, "POST", {"songInfo": song, "quality": "320k"})
    ok_full = isinstance(full, dict) and bool(full.get("url"))
    results.append(("url user+token", status, ok_full))
    print(f"[3] music/url user+token -> {status} url={'有' if ok_full else '无'} type={full.get('type') if isinstance(full, dict) else '-'}")

    status, only = call("/api/music/url", AUTH_TOKEN_ONLY, None, "POST", {"songInfo": song, "quality": "320k"})
    expected_fail = status >= 400 or not (isinstance(only, dict) and only.get("url"))
    results.append(("url token-only 应失败", status, expected_fail))
    print(f"[4] music/url token-only -> {status}（预期失败，验证用户名必需）{'' if expected_fail else '  ← 与预期不符'}")

    if ok_full:
        query = urllib.parse.urlencode({"url": full["url"], "filename": "live_check", "tag": "1",
                                        "name": song.get("name"), "singer": song.get("singer")})
        req = urllib.request.Request(HOST + "/api/music/download?" + query)
        for key, value in AUTH_FULL.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=90, context=CTX) as resp:
                head = resp.read(2048)
                results.append(("download", resp.status, len(head) > 0))
                print(f"[5] download -> {resp.status} content-type={resp.headers.get('content-type')} 首块={len(head)}B head={head[:16]!r}")
        except Exception as err:  # noqa: BLE001
            results.append(("download", -1, False))
            print(f"[5] download 失败：{type(err).__name__}: {err}")

    print("\n=== 汇总 ===")
    for name, status, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  (status={status})")
    return 0 if all(ok for _, _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
