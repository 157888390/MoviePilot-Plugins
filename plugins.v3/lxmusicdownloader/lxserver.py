# -*- coding: utf-8 -*-
"""LX Sync Server API 客户端。

不再在插件里运行洛雪自定义源 JavaScript，改为调用自建 LX Sync Server 的 HTTP 接口：
搜索、解析播放直链、代理下载（服务端注入 ID3）、服务器缓存下载。

接口约定（与 XCQ0607/lxserver 的 `src/server/server.ts` 对齐）：
- ``GET  /api/user/auth/verify``            校验 token，可反查绑定的 username
- ``POST /api/user/login``                  ``{username, password}`` -> ``{success, token}``
- ``GET  /api/music/search``                ``name/source/type/page/pages/limit``，**返回裸数组**
- ``POST /api/music/url``                   ``{songInfo, quality}`` -> ``{url, quality}``
- ``GET  /api/music/download``              代理下载，``tag=1`` 注入 ID3，响应为二进制流
- ``POST /api/music/cache/download``        服务端缓存下载
- ``GET  /api/music/cache/stats``           缓存统计
- ``GET  /api/music/songList/tags``         歌单标签
- ``GET  /api/music/songList/list``         按标签浏览歌单
- ``GET  /api/music/songList/detail``       歌单详情（含曲目列表）
- ``GET  /api/music/songList/search``       搜索歌单
- ``GET  /api/custom-source/list``          已启用的自定义音源（用于诊断 500 错误）

歌单相关的 5 个接口全部由服务端内置 musicSdk 提供，**与自定义音源脚本无关**，
因此即使直链解析不可用，歌单浏览仍然可用。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import httpx2

from app.sdk.logging import logger

# 音质 -> 常见扩展名。仅作兜底，真实容器以响应文件头嗅探为准
QUALITY_EXT = {
    "128k": ".mp3",
    "192k": ".mp3",
    "320k": ".mp3",
    "flac": ".flac",
    "flac24bit": ".flac",
    "hires": ".flac",
    "ape": ".ape",
    "wav": ".wav",
    "dts": ".dts",
    "ogg": ".ogg",
}

SUPPORTED_SOURCES = {
    "kw": "酷我",
    "kg": "酷狗",
    "tx": "QQ 音乐",
    "wy": "网易云",
    "mg": "咪咕",
}

# 平台对 songList 各方法的支持矩阵。服务端对不支持的方法**不做优雅降级**，
# 直接抛 `Source X does not support songList`（HTTP 500），所以插件侧先挡掉，
# 免得让用户看到一句无从下手的报错。
SONGLIST_CAPABILITY = {
    # platform: (tags, list, detail, search)
    "wy": ("tags", "list", "detail", "search"),
    "tx": ("tags", "list", "detail", "search"),
    "kg": ("tags", "list", "detail", "search"),
    "kw": ("tags", "list", "detail"),
    "mg": ("tags", "list", "detail", "search"),
    "bd": ("tags", "list", "detail"),
}

# 各平台歌单详情单页条数：wy 为 1000，其余多为 30。翻页步长按此估算，
# 真实条数仍以响应里的 limit/total 为准。
SONGLIST_PAGE_SIZE = {"wy": 1000}

HTTP_HINTS = {
    401: "鉴权失败：x-user-name 与 x-user-token 必须成对提供，且 token 需与用户名匹配",
    403: "权限不足：该账号可能没有启用自定义音源或服务器缓存权限",
    404: "接口不存在：请确认 LX 服务端版本是否支持该路径",
    500: "服务端内部错误，通常是自定义音源未启用或上游解析失败",
}


class LxServerError(Exception):
    """LX 服务端调用失败。"""


class LxServerClient:
    """LX Sync Server 的薄封装，无状态、可重复创建。"""

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        token: str = "",
        timeout: float = 30.0,
        download_timeout: float = 300.0,
    ) -> None:
        self.host = (host or "").strip().rstrip("/")
        self.username = (username or "").strip()
        self.password = password or ""
        self.token = (token or "").strip()
        self.timeout = timeout
        self.download_timeout = download_timeout

        if not self.host:
            raise LxServerError("未配置 LX 服务端地址")
        if not self.host.startswith(("http://", "https://")):
            raise LxServerError(f"服务端地址必须以 http:// 或 https:// 开头：{self.host}")

        # 没有 token 但有账号密码时自动登录，省去手工换取 token
        if not self.token and self.username and self.password:
            self.login()
        # 只有 token 没有用户名时，用 verify 反查 token 绑定的用户。
        # 这一步是必须的：/api/music/search 不需要鉴权，但 /api/music/url 靠
        # x-user-name 定位该用户名下的自定义音源，仅带 token 会退回公开用户并报
        # 「未找到支持 xx 平台的自定义源」。
        elif self.token and not self.username:
            try:
                self.verify()
            except LxServerError as err:
                logger.warn(f"token 已配置但未能反查用户名，将按公开用户访问：{err}")

    # ------------------------------------------------------------------ #
    #                            基础请求                                  #
    # ------------------------------------------------------------------ #
    @property
    def authenticated(self) -> bool:
        """是否带上了完整鉴权信息。"""
        return bool(self.username and self.token)

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """构造请求头。

        有 token 就发送：verify 能据 token 反查用户名；而 /api/music/url 等业务
        接口靠 x-user-name 定位音源，因此用户名已知时一并带上。
        """
        headers = {"Accept": "application/json"}
        if self.token:
            headers["x-user-token"] = self.token
            if self.username:
                headers["x-user-name"] = self.username
        if extra:
            headers.update(extra)
        return headers

    def _get(self, path: str, params: Optional[dict] = None, extra_headers: Optional[dict] = None) -> Any:
        return self._request("GET", path, params=params, extra_headers=extra_headers)

    def _post(self, path: str, body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> Any:
        return self._request("POST", path, json_body=body, extra_headers=extra_headers)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> Any:
        """发起请求并按状态码给出可读错误。"""
        url = self.host + path
        try:
            if method == "GET":
                response = httpx2.get(
                    url,
                    params=params,
                    headers=self._headers(extra_headers),
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            else:
                response = httpx2.post(
                    url,
                    json=json_body,
                    headers=self._headers(extra_headers),
                    timeout=self.timeout,
                    follow_redirects=True,
                )
        except Exception as err:  # noqa: BLE001
            raise LxServerError(f"无法连接 LX 服务端 {self.host}：{err}") from err

        if response.status_code >= 400:
            raise LxServerError(self._explain(response, path))

        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return response.text

    @staticmethod
    def _explain(response: "httpx2.Response", path: str) -> str:
        """把服务端错误翻译成一句能直接定位问题的提示。"""
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("error") or payload.get("message") or payload)
            else:
                detail = str(payload)
        except Exception:  # noqa: BLE001
            detail = (response.text or "")[:300]

        hint = HTTP_HINTS.get(response.status_code, "")
        if "自定义源" in detail:
            hint = "服务端未启用支持该平台的自定义源，请在 LX 面板导入并启用对应音源脚本"
        elif path.endswith("/search") and response.status_code >= 500:
            # 实测 QQ 音乐搜索长期返回 500，而其它平台正常：属于服务端该平台的上游故障，
            # 换平台即可，不必让用户去排查自定义源。
            hint = "该平台的搜索接口在服务端不可用，请更换音源平台后重试"
        suffix = f"（{hint}）" if hint else ""
        return f"HTTP {response.status_code} {path} -> {detail}{suffix}"

    # ------------------------------------------------------------------ #
    #                              鉴权                                    #
    # ------------------------------------------------------------------ #
    def login(self) -> str:
        """用用户名密码换取 token。"""
        if not self.username or not self.password:
            raise LxServerError("缺少用户名或密码，无法登录")

        data = self._post("/api/user/login", {"username": self.username, "password": self.password})
        if not isinstance(data, dict) or not data.get("success"):
            raise LxServerError(f"登录失败：{data}")
        token = str(data.get("token") or "")
        if not token:
            raise LxServerError("登录成功但响应中没有 token")
        self.token = token
        logger.info(f"LX 服务端登录成功：{data.get('username')}")
        return token

    def verify(self) -> dict[str, Any]:
        """校验当前凭据，并纠正 token 实际绑定的用户名。"""
        data = self._get("/api/user/auth/verify")
        if not isinstance(data, dict):
            return {}
        if data.get("valid"):
            real_user = data.get("username")
            if real_user and real_user != self.username:
                logger.info(f"Token 实际绑定用户为 {real_user}，已自动纠正")
                self.username = str(real_user)
        return data

    # ------------------------------------------------------------------ #
    #                              业务                                    #
    # ------------------------------------------------------------------ #
    def search(
        self,
        name: str,
        source: str = "kw",
        search_type: str = "song",
        page: int = 1,
        pages: int = 1,
        limit: int = 20,
    ) -> list[dict]:
        """搜索歌曲。该接口返回裸数组，不是 {list: [...]}。

        服务端的 song 分支每页固定 20 条，``limit`` 对它不生效；要拿到更多候选
        必须加大 ``pages``。这里按需反推页数，再在本地截断到 limit。
        """
        if search_type == "song" and limit > 0:
            pages = max(int(pages), -(-limit // 20))

        data = self._get(
            "/api/music/search",
            params={
                "name": name,
                "source": source,
                "type": search_type,
                "page": page,
                "pages": pages,
                "limit": limit,
            },
        )

        items: list[dict] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            for key in ("list", "data", "songs", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    items = [item for item in value if isinstance(item, dict)]
                    break
        else:
            logger.warn(f"搜索响应结构无法识别：{str(data)[:200]}")
            return []

        # 服务端并不严格按 limit 截断（实测请求 limit=5 仍返回 20 条），
        # 这里补一次本地截断，保证插件的「搜索结果数」配置真正生效。
        if limit and len(items) > limit:
            return items[:limit]
        return items

    def music_url(self, song_info: dict, quality: str) -> dict:
        """解析播放直链。该接口强依赖服务端已启用的自定义音源。"""
        data = self._post(
            "/api/music/url",
            {"songInfo": song_info, "quality": quality},
            extra_headers={"x-req-id": uuid.uuid4().hex},
        )
        if not isinstance(data, dict):
            raise LxServerError(f"直链解析响应异常：{str(data)[:200]}")
        return data

    # ------------------------------------------------------------------ #
    #                              歌单                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def supports(source: str, method: str) -> bool:
        """该平台是否支持某个 songList 方法。未收录的平台一律放行，交给服务端判定。"""
        capabilities = SONGLIST_CAPABILITY.get(str(source or "").lower())
        if capabilities is None:
            return True
        return method in capabilities

    def _songlist_get(self, path: str, params: dict, source: str, method: str) -> Any:
        """统一的 songList 请求入口，把平台不支持翻译成人话。"""
        if not self.supports(source, method):
            label = SUPPORTED_SOURCES.get(source, source)
            raise LxServerError(f"{label}（{source}）不支持「{method}」歌单操作，请更换音源平台")
        return self._get(path, params=params)

    def songlist_tags(self, source: str = "wy") -> dict:
        """歌单标签与排序方式列表。"""
        data = self._songlist_get(
            "/api/music/songList/tags", {"source": source}, source, "tags"
        )
        return data if isinstance(data, dict) else {"raw": data}

    def songlist_list(
        self,
        source: str = "wy",
        tag_id: str = "",
        sort_id: str = "hot",
        page: int = 1,
    ) -> list[dict]:
        """按标签浏览歌单。返回的每项是歌单摘要（含 id / name / img）。"""
        data = self._songlist_get(
            "/api/music/songList/list",
            {"source": source, "tagId": tag_id, "sortId": sort_id or "hot", "page": page},
            source,
            "list",
        )
        return self._as_songlist(data)

    def songlist_search(self, text: str, source: str = "wy", page: int = 1) -> list[dict]:
        """按关键词搜索歌单。返回的每项是歌单摘要。"""
        data = self._songlist_get(
            "/api/music/songList/search",
            {"text": text, "source": source, "page": page},
            source,
            "search",
        )
        return self._as_songlist(data)

    def songlist_detail(self, playlist_id: str, source: str = "wy", page: int = 1) -> dict:
        """取歌单详情（含曲目列表）。

        ``playlist_id`` 既可以是纯 id，也可以是歌单链接——各平台模块会用正则
        从 URL 里提取 id（wy 还支持 ``id###token`` 注入 cookie 访问私密歌单）。
        """
        data = self._songlist_get(
            "/api/music/songList/detail",
            {"id": playlist_id, "source": source, "page": page},
            source,
            "detail",
        )
        if not isinstance(data, dict):
            raise LxServerError(f"歌单详情响应异常：{str(data)[:200]}")
        return data

    def songlist_all(self, playlist_id: str, source: str = "wy", max_songs: int = 1000) -> dict:
        """翻页拉取歌单全量曲目。

        wy 单页 1000 首、其余平台多为 30 首，大歌单必须循环翻页直到
        ``page * limit >= total``。``max_songs`` 是安全上限，防止超大歌单把
        插件拖死（服务端有一个 limit_song=100000 的极端场景）。
        """
        first = self.songlist_detail(playlist_id, source=source, page=1)
        songs = [item for item in (first.get("list") or []) if isinstance(item, dict)]
        info = first.get("info") if isinstance(first.get("info"), dict) else {}

        try:
            total = int(first.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        try:
            limit = int(first.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            limit = SONGLIST_PAGE_SIZE.get(source, 30)

        page = 1
        while total > 0 and len(songs) < min(total, max_songs):
            page += 1
            chunk = self.songlist_detail(playlist_id, source=source, page=page)
            batch = [item for item in (chunk.get("list") or []) if isinstance(item, dict)]
            if not batch:
                break
            songs.extend(batch)
            # 服务端给的 limit 可能与实际返回条数不符，用实际值兜底防死循环
            if len(batch) < limit:
                break

        if len(songs) > max_songs:
            songs = songs[:max_songs]

        return {
            "songs": songs,
            "info": info,
            "total": total or len(songs),
            "truncated": bool(total and total > len(songs)),
            "source": source,
            "id": str(playlist_id),
        }

    @staticmethod
    def _as_songlist(data: Any) -> list[dict]:
        """把 songList/list 与 search 的响应归一化成歌单摘要数组。"""
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("list", "data", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def open_download(self, play_url: str, filename: str, song_info: dict, embed_tag: bool = True, embed_lyric: bool = False):
        """以流式方式打开代理下载，需在 with 语句中使用。

        ``tag=1`` 让服务端用 node-id3 写入封面与文字标签，是**纯透传 + 追加标签**，
        不会重新编码音频；``lyric=1`` 额外嵌入歌词，需要同时给出 source 与 songmid，
        服务端取歌词失败时会静默降级，不影响下载本身。
        """
        params = {
            "url": play_url,
            "filename": filename,
            "name": song_info.get("name") or "",
            "singer": song_info.get("singer") or "",
            "album": song_info.get("albumName") or "",
            "pic": song_info.get("img") or "",
        }
        if embed_tag:
            params["tag"] = "1"
            if embed_lyric and song_info.get("songmid"):
                params["lyric"] = "1"
                params["source"] = str(song_info.get("source") or "")
                params["songmid"] = str(song_info["songmid"])
                # kg 用 hash 定位歌词，kw/mg 靠 songmid 即可
                if song_info.get("hash"):
                    params["hash"] = str(song_info["hash"])
                if song_info.get("interval"):
                    params["interval"] = str(song_info["interval"])

        return httpx2.stream(
            "GET",
            self.host + "/api/music/download",
            params=params,
            headers=self._headers(),
            timeout=self.download_timeout,
            follow_redirects=True,
        )

    def open_raw(self, url: str, referer: str = ""):
        """直接下载任意 URL（用于取封面），需在 with 语句中使用。"""
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        return httpx2.stream(
            "GET", url, headers=headers, timeout=self.download_timeout, follow_redirects=True
        )

    def cache_download(
        self,
        song_info: dict,
        play_url: str,
        quality: str,
        cache_lyric: bool = True,
        embed_lyric: bool = True,
    ) -> dict:
        """提交服务端缓存下载任务。"""
        data = self._post(
            "/api/music/cache/download",
            {
                "songInfo": song_info,
                "url": play_url,
                "quality": quality,
                "cacheLyric": cache_lyric,
                "embedLyric": embed_lyric,
            },
        )
        return data if isinstance(data, dict) else {"raw": data}

    def cache_stats(self) -> dict:
        """查询服务端缓存统计。"""
        data = self._get("/api/music/cache/stats")
        return data if isinstance(data, dict) else {"raw": data}

    def custom_sources(self) -> Any:
        """列出服务端已启用的自定义音源，用于诊断解析失败。

        注意该接口与其余接口不同：**用户取自 URL 参数 username**（缺省 'default'），
        并不读取 x-user-name 头。不显式传 username 会拿到 default 用户的列表而恒为空，
        导致误判「服务端没有启用任何音源」。
        """
        try:
            return self._get(
                "/api/custom-source/list",
                params={"username": self.username or "default"},
            )
        except LxServerError as err:
            return {"error": str(err)}
