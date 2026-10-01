# -*- coding: utf-8 -*-
"""LX 音源下载插件。

通过自建 LX Sync Server 的 HTTP API 完成歌曲搜索、歌单浏览、直链解析与下载，
支持自定义下载位置，并通过远程命令 `/lx_search`、`/lx_download`、`/lx_playlist`、
`/lx_stats` 响应请求。

插件不再在本地运行洛雪自定义源 JavaScript，音源能力由服务端提供，
因此运行环境不需要 Node.js。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger

from .downloader import LxDownloader, split_artists
from .lxserver import SUPPORTED_SOURCES, LxServerClient, LxServerError

# 单次搜索缓存的结果数，供远程命令按序号下载
MAX_CANDIDATES = 10

# 音质降级优先级：请求的音质不可用时按此顺序挑最接近的
QUALITY_PREFERENCE = ["flac24bit", "hires", "flac", "320k", "192k", "128k"]

# 歌单整单下载的兜底上限。远程命令里一次性下载几千首没有意义，
# 也会把消息渠道刷爆；确需全量请走「歌单浏览 → 勾选下载」。
COMMAND_PLAYLIST_LIMIT = 50

# 缺参数时回给用户的用法提示。键是 event_data 里的 action，值是**真实命令名**——
# 注意 action 本身不带斜杠（如 "lx_search"），直接拼进文案会得到 "lx_search"
# 这种既不能点也不能复制的残缺命令，必须写成 "/lx_search"。
COMMAND_USAGE = {
    "lx_search": "/lx_search 歌曲名",
    "lx_download": "/lx_download 序号，或 /lx_download 歌手 - 歌名",
    "lx_playlist": "/lx_playlist 歌单名 / 歌单链接；/lx_playlist dl 歌单名 下载前 50 首",
    "lx_stats": "/lx_stats",
}


class LxMusicDownloader(_PluginBase):
    """LX 音源下载插件主类。"""

    plugin_name = "LX 音源下载"
    plugin_desc = "调用自建 LX Sync Server，搜索歌曲、浏览歌单并下载到自定义目录。"
    # 插件标签（与 package.v3.json 的 labels 保持一致）
    plugin_label = "音乐,下载,歌单,LX音源"
    # 自定义图标必须写成完整 URL：裸文件名只会去官方库 icons/ 里找，找不到就回退成拼图占位图
    plugin_icon = (
        "https://raw.githubusercontent.com/157888390/MoviePilot-Plugins"
        "/main/icons/lxmusicdownloader.png"
    )
    plugin_version = "3.2.1"
    plugin_author = "157888390"
    author_url = "https://github.com/157888390"
    plugin_config_prefix = "lxmusicdownloader_"
    plugin_order = 61
    auth_level = 1

    _enabled = False
    _sidebar_enabled = True          # 是否在主界面左侧导航栏显示入口
    _client: Optional[LxServerClient] = None
    _lock: Optional[threading.Lock] = None
    # 会话 -> 最近一次搜索结果，供 /lx_download <序号> 使用。
    # 类属性保持 None：这样首次 init_plugin 一定会落到实例属性上，
    # 避免虚拟分身之间共享同一个可变 dict（V3 要求按运行实例隔离状态）。
    _last_results: Optional[dict[str, list[dict]]] = None

    # ------------------------------------------------------------------ #
    #                            生命周期                                  #
    # ------------------------------------------------------------------ #
    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置并建立本次运行状态，允许重复调用。"""
        config = config or {}

        self._enabled = bool(config.get("enabled"))
        self._host = str(config.get("host") or "").strip()
        self._username = str(config.get("username") or "").strip()
        self._password = str(config.get("password") or "")
        self._token = str(config.get("token") or "").strip()
        self._source = str(config.get("source") or "kw")
        self._quality = str(config.get("quality") or "320k")
        self._download_path = str(config.get("download_path") or "")
        self._name_template = str(config.get("name_template") or "{name} - {singer}")
        self._subdir_by_artist = bool(config.get("subdir_by_artist"))
        self._save_cover = bool(config.get("save_cover"))
        self._use_server_cache = bool(config.get("use_server_cache"))
        self._embed_tag = bool(config.get("embed_tag"))
        self._embed_lyric = bool(config.get("embed_lyric"))
        self._split_artists = bool(config.get("split_artists", True))
        self._sidebar_enabled = bool(config.get("sidebar_enabled", True))
        try:
            self._max_results = max(1, min(int(config.get("max_results") or MAX_CANDIDATES), 50))
        except (TypeError, ValueError):
            self._max_results = MAX_CANDIDATES
        try:
            # 与服务端下载队列默认并发（3）保持一致，避免把上游音源打爆
            self._playlist_concurrency = max(1, min(int(config.get("playlist_concurrency") or 3), 8))
        except (TypeError, ValueError):
            self._playlist_concurrency = 3

        if self._lock is None:
            self._lock = threading.Lock()
        if not isinstance(self._last_results, dict):
            self._last_results = {}

        # 配置变化后必须丢弃旧客户端，否则会继续用上一份凭据
        self._client = None
        self._downloader = LxDownloader()

        if self._enabled:
            try:
                self.get_client()
            except LxServerError as err:
                logger.warn(f"LX 服务端初始化失败，将在首次使用时重试：{err}")

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return bool(self._enabled)

    def stop_service(self) -> None:
        """释放后台资源，可重复调用。"""
        self._enabled = False
        self._client = None
        self._last_results = {}

    # ------------------------------------------------------------------ #
    #                            远程命令                                  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """注册插件远程命令。cmd 必须以 / 开头且不能有前导空格。"""
        return [
            {
                "cmd": "/lx_search",
                "event": EventType.PluginAction,
                "desc": "搜索歌曲",
                "category": "音乐下载",
                "data": {"action": "lx_search"},
            },
            {
                "cmd": "/lx_download",
                "event": EventType.PluginAction,
                "desc": "下载歌曲",
                "category": "音乐下载",
                "data": {"action": "lx_download"},
            },
            {
                "cmd": "/lx_playlist",
                "event": EventType.PluginAction,
                "desc": "浏览/下载歌单",
                "category": "音乐下载",
                "data": {"action": "lx_playlist"},
            },
            {
                "cmd": "/lx_stats",
                "event": EventType.PluginAction,
                "desc": "服务端缓存状态",
                "category": "音乐下载",
                "data": {"action": "lx_stats"},
            },
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_plugin_action(self, event: Event) -> None:
        """只处理属于本插件的动作，其余动作直接返回。"""
        event_data = event.event_data or {}
        action = event_data.get("action")
        if action not in ("lx_search", "lx_download", "lx_playlist", "lx_stats"):
            return

        if not self.get_state():
            self._reply(event, "LX 音源下载", "插件未启用，请先在插件配置中开启。")
            return

        # MP v3 的 app/command.py __run_command() 把命令参数放进 **arg_str**：
        #     if data_str: data["arg_str"] = data_str
        # 事件载荷 PluginActionEventData 本身并没有 args/arg 字段。只读 args/arg
        # 会恒为空，表现为「带参数也一直回用法提示」，所以必须以 arg_str 为准，
        # 其余键只作向后兼容。
        args = str(
            event_data.get("arg_str")
            or event_data.get("args")
            or event_data.get("arg")
            or ""
        ).strip()

        try:
            if action == "lx_stats":
                self._reply(event, "服务端缓存", self._do_stats())
                return

            if action == "lx_playlist":
                self._reply(event, "歌单", self._do_playlist(args))
                return

            if not args:
                usage = COMMAND_USAGE.get(action, "/lx_search 歌曲名")
                self._reply(event, "LX 音源下载", f"用法：{usage}")
                return

            if action == "lx_search":
                self._reply(event, "搜索结果", self._do_search(event, args))
            else:
                self._reply(event, "下载结果", self._do_download(event, args))
        except LxServerError as err:
            logger.error(f"LX 命令执行失败：{err}")
            self._reply(event, "LX 音源下载", f"执行失败：{err}")
        except Exception as err:  # noqa: BLE001
            logger.error(f"LX 命令执行异常：{err}")
            self._reply(event, "LX 音源下载", f"执行异常：{err}")

    def _do_search(self, event: Event, keyword: str) -> str:
        """搜索并把候选缓存到内存，供后续按序号下载。"""
        songs = self.get_client().search(keyword, source=self._source, limit=self._max_results)
        if not songs:
            return f"「{self._source}」没有搜索到与「{keyword}」相关的歌曲。"

        self._last_results[self._result_key(event)] = songs
        # 提示必须写明「带命令前缀」：MP 的媒体交互链会吞掉裸数字
        # （app/chain/interaction.py:278 isdigit 分支，无活动会话时回「输入有误！」
        # 并消费消息，UserMessage 事件不触发），所以直接回复序号永远到不了插件。
        lines = [f"共 {len(songs)} 条结果，回复 /lx_download 序号 下载对应歌曲（如 /lx_download 1，直接回复数字无效）："]
        for index, song in enumerate(songs, start=1):
            qualities = "/".join(item.get("type", "") for item in song.get("types") or [])
            lines.append(
                f"{index}. {song.get('name')} - {song.get('singer')}"
                f"（{song.get('albumName') or '未知专辑'}｜{qualities or '未知音质'}）"
            )
        return "\n".join(lines)

    def _do_download(self, event: Event, args: str) -> str:
        """按序号或关键词解析并下载歌曲。"""
        key = self._result_key(event)
        if args.isdigit():
            songs = self._last_results.get(key) or []
            position = int(args)
            if not 1 <= position <= len(songs):
                return f"序号 {position} 超出范围，请先执行 /lx_search 搜索。"
            song = songs[position - 1]
        else:
            songs = self.get_client().search(args, source=self._source, limit=1)
            if not songs:
                return f"「{self._source}」没有搜索到与「{args}」相关的歌曲。"
            song = songs[0]

        return self._download_song(song)

    def _do_playlist(self, args: str) -> str:
        """歌单远程命令。

        - ``/lx_playlist 歌单名``      搜索歌单，列出候选
        - ``/lx_playlist <链接或ID>``  查看歌单曲目
        - ``/lx_playlist dl 歌单名``   下载前若干首
        """
        if not args:
            client = self.get_client()
            rows = client.songlist_list(source=self._source, sort_id="hot", page=1)
            if not rows:
                return f"「{self._source}」没有取到热门歌单。"
            lines = [f"「{SUPPORTED_SOURCES.get(self._source, self._source)}」热门歌单（前 10）："]
            for index, row in enumerate(rows[:10], start=1):
                lines.append(f"{index}. {row.get('name')}  id={row.get('id')}")
            lines.append("用法：/lx_playlist dl 歌单名｜/lx_playlist <歌单链接>")
            return "\n".join(lines)

        download_first = False
        if args.lower().startswith(("dl ", "down ")):
            download_first = True
            args = args.split(" ", 1)[1].strip()
        if not args:
            return "用法：/lx_playlist dl 歌单名"

        # 像链接/纯数字 ID 就直接当歌单标识，否则当关键词搜歌单
        if self._looks_like_playlist_id(args):
            playlist_id, source = args, self._source
        else:
            rows = self.get_client().songlist_search(args, source=self._source, page=1)
            if not rows:
                return f"「{self._source}」没有搜索到与「{args}」相关的歌单。"
            if not download_first:
                lines = [f"「{args}」匹配到 {len(rows)} 个歌单："]
                for index, row in enumerate(rows[:10], start=1):
                    author = row.get("author") or row.get("creator") or ""
                    lines.append(f"{index}. {row.get('name')}（{author}）\n    id={row.get('id')}")
                lines.append("下载：/lx_playlist dl " + args)
                return "\n".join(lines)
            playlist_id = str(rows[0].get("id") or "")
            source = str(rows[0].get("source") or self._source)
            if not playlist_id:
                return f"歌单「{rows[0].get('name')}」没有返回 id，无法下载。"

        if not download_first:
            payload = self.fetch_playlist(playlist_id, source=source, max_songs=COMMAND_PLAYLIST_LIMIT)
            info = payload.get("info") or {}
            songs = payload["songs"]
            lines = [
                f"歌单：{info.get('name') or playlist_id}",
                f"共 {payload.get('total') or len(songs)} 首"
                + ("（仅显示前 50）" if payload.get("truncated") else ""),
            ]
            for index, song in enumerate(songs[:30], start=1):
                lines.append(f"{index}. {song.get('name')} - {song.get('singer')}")
            if len(songs) > 30:
                lines.append(f"... 其余 {len(songs) - 30} 首请在插件页查看")
            lines.append("下载前 50 首：/lx_playlist dl " + (info.get("name") or playlist_id))
            return "\n".join(lines)

        started = time.time()
        result = self.download_playlist(
            playlist_id,
            source=source,
            quality=self._quality,
            concurrency=self._playlist_concurrency,
            skip_existing=True,
            limit=COMMAND_PLAYLIST_LIMIT,
        )
        lines = [
            f"歌单「{result['name']}」下载完成，用时 {time.time() - started:.0f}s",
            f"成功 {result['success']}｜跳过（已存在）{result['skipped']}｜失败 {result['failed']}"
            f"｜共处理 {result['total']} 首",
        ]
        if result.get("limited"):
            lines.append(f"（歌单共 {result.get('playlist_total')} 首，远程命令只处理前 {result['total']} 首）")
        failures = [item for item in result["items"] if item["status"] == "failed"]
        for item in failures[:8]:
            lines.append(f"✗ {item['name']}：{item['message'][:80]}")
        if len(failures) > 8:
            lines.append(f"... 另有 {len(failures) - 8} 首失败，详见插件页")
        return "\n".join(lines)

    @staticmethod
    def _looks_like_playlist_id(value: str) -> bool:
        """判断参数是歌单标识（URL 或纯数字 ID）而不是搜索关键词。"""
        text = (value or "").strip()
        if not text:
            return False
        if text.isdigit():
            return True
        return text.startswith(("http://", "https://")) or "/playlist/" in text or "/songlist/" in text

    def _download_song(self, song: dict, quality: Optional[str] = None) -> str:
        """解析直链并落盘，返回给用户的结果文案。quality 为空时用插件配置的音质。"""
        client = self.get_client()
        quality = self._pick_quality(song, quality or self._quality)

        info = client.music_url(song, quality)
        play_url = info.get("url")
        if not play_url:
            detail = info.get("error") or str(info)[:200]
            raise LxServerError(f"未获取到播放直链：{detail}")

        # 服务端会在自定义源解析失败时自动降级（flac24bit -> flac -> 320k ...），
        # 真实到手的音质在 type 字段里，quality 实际并不存在。
        real_quality = str(info.get("type") or info.get("quality") or quality)
        requested = str(info.get("requestedSource") or song.get("source") or "")

        if self._use_server_cache:
            result = client.cache_download(song, play_url, real_quality)
            return (
                f"《{song.get('name')}》已提交服务端缓存任务（{real_quality}）。\n"
                f"用 /lx_stats 查看进度。响应的 data：{str(result)[:180]}"
            )

        return self._download_local(song, play_url, real_quality)

    def _download_local(self, song: dict, play_url: str, quality: str) -> str:
        """通过服务端代理下载到本地目录。"""
        client = self.get_client()
        base_name = LxDownloader.build_filename(song, self._name_template)
        target_dir = self._resolve_download_dir(song)
        # 「许嵩、何曼婷」这种拼接串必须拆成多值写进标签，否则 MoviePilot 会当成
        # 一个整体艺术家，与 MusicBrainz 的候选求不到交集，识别失败导致整理被拒。
        artists = split_artists(song.get("singer")) if self._split_artists else []

        with client.open_download(
            play_url, base_name, song, embed_tag=self._embed_tag, embed_lyric=self._embed_lyric
        ) as response:
            # 下载接口的错误也是 JSON 体，必须在落盘前判定，否则会把错误页写成音频文件
            if response.status_code >= 400:
                detail = self._read_error_body(response)
                raise LxServerError(f"代理下载失败：HTTP {response.status_code} {detail}")
            saved, size = self._downloader.save_stream(response, target_dir, base_name, quality, artists)

        if size < 4096:
            saved.unlink(missing_ok=True)
            raise LxServerError(f"下载内容仅 {size} 字节，判定为失败，已删除残缺文件")

        lines = [f"《{song.get('name')}》下载完成（{quality}）", f"路径：{saved}", f"大小：{size / 1024 / 1024:.2f} MB"]

        if self._save_cover and song.get("img"):
            try:
                with client.open_raw(str(song["img"])) as cover:
                    cover.raise_for_status()
                    cover_path = self._downloader.save_cover(cover, saved)
                if cover_path:
                    lines.append(f"封面：{cover_path.name}")
            except Exception as err:  # noqa: BLE001
                logger.warn(f"封面下载失败：{err}")

        return "\n".join(lines)

    @staticmethod
    def _read_error_body(response) -> str:
        """从流式错误响应里读出可读的错误详情。"""
        try:
            raw = response.read()
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return str(payload.get("error") or payload.get("message") or payload)[:200]
            except Exception:  # noqa: BLE001
                pass
            return text[:200]
        except Exception:  # noqa: BLE001
            return "(无法读取错误详情)"

    def _do_stats(self) -> str:
        """查询服务端缓存统计。"""
        data = self.get_client().cache_stats()
        return "服务端缓存统计：\n" + str(data)[:800]

    def _reply(self, event: Event, title: str, text: str) -> None:
        """把结果回复到命令来源渠道，没有渠道时降级为系统通知。

        ⚠️ 必须带上发起用户。MP v3 的 `app/chain/_messaging.py` 里：

            if message.userid:
                return None          # 有用户 -> 直接投递给该用户
            return get_notification_switch(message.mtype) or "admin"

        userid 为空时会退化成「发送给管理员」的广播路由走另一条通道，这条路
        径出问题就会静默收不到回复。命令事件的载荷里用户字段叫 **user**
        （`app/command.py`：`data["user"] = userid`），不是 userid。
        """
        event_data = event.event_data or {}
        self.post_message(
            channel=event_data.get("channel"),
            mtype=NotificationType.Plugin,
            title=title,
            text=text,
            userid=self._command_user(event),
        )

    @staticmethod
    def _command_user(event: Event) -> Any:
        """取出命令发起用户，兼容不同版本的载荷字段名。"""
        event_data = event.event_data or {}
        return event_data.get("user") or event_data.get("userid")

    @staticmethod
    def _result_key(event: Event) -> str:
        """按来源会话隔离搜索结果缓存。"""
        event_data = event.event_data or {}
        # 同样是 user 而不是 userid：取不到会让所有用户共用同一个「anonymous」
        # 键，A 搜索出的候选会被 B 的 /lx_download 序号 命中。
        user = event_data.get("user") or event_data.get("userid") or "anonymous"
        return f"{event_data.get('channel') or 'system'}:{user}"

    # ------------------------------------------------------------------ #
    #                            客户端与路径                              #
    # ------------------------------------------------------------------ #
    def get_client(self) -> LxServerClient:
        """返回 LX 服务端客户端，首次调用时创建。"""
        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is not None:
                return self._client
            self._client = LxServerClient(
                host=self._host,
                username=self._username,
                password=self._password,
                token=self._token,
            )
        return self._client

    @staticmethod
    def _pick_quality(song: dict, requested: str) -> str:
        """请求音质不可用时按优先级降级，避免必然失败的请求。"""
        available = [str(item.get("type") or "") for item in song.get("types") or []]
        if not available or requested in available:
            return requested
        for candidate in QUALITY_PREFERENCE:
            if candidate in available:
                logger.info(f"音质 {requested} 不可用，降级为 {candidate}")
                return candidate
        return available[0]

    def _resolve_download_dir(self, song: Optional[dict] = None) -> Path:
        """解析下载目录，未配置时回落到插件数据目录下的 music。

        歌单整单下载与单曲下载都落在同一个扁平目录：目录整理交给 MoviePilot
        自身的媒体整理能力，插件不再自造一套分类规则，避免两边规则打架。
        """
        base = Path(self._download_path).expanduser() if self._download_path else self.get_data_path() / "music"
        if self._subdir_by_artist and song:
            singer = (song.get("singer") or "").split("&")[0].strip()
            if singer:
                base = base / LxDownloader.sanitize(singer)
        return base

    # ------------------------------------------------------------------ #
    #                          歌单整单下载                                #
    # ------------------------------------------------------------------ #
    def fetch_playlist(
        self,
        playlist_id: str,
        source: str = "",
        max_songs: int = 1000,
    ) -> dict:
        """拉取歌单全量曲目（含翻页），返回 ``{songs, info, total, ...}``。"""
        source = (source or self._source or "wy").strip()
        playlist_id = str(playlist_id or "").strip()
        if not playlist_id:
            raise LxServerError("缺少歌单 ID 或链接")
        return self.get_client().songlist_all(playlist_id, source=source, max_songs=max_songs)

    def download_playlist(
        self,
        playlist_id: str,
        source: str = "",
        quality: Optional[str] = None,
        concurrency: int = 3,
        skip_existing: bool = True,
        max_songs: int = 1000,
        limit: int = 0,
    ) -> dict:
        """整单下载：并发解析 + 落盘，逐首给出成功/失败明细。

        ``limit > 0`` 时只取歌单前 N 首（远程命令用）。并发默认 3，与服务端
        下载队列的默认并发保持一致，避免把上游打爆。
        """
        payload = self.fetch_playlist(playlist_id, source=source, max_songs=max_songs)
        songs: list[dict] = payload["songs"]
        if limit and limit > 0:
            songs = songs[:limit]

        info = payload.get("info") or {}
        playlist_name = str(info.get("name") or "").strip()
        if not playlist_name:
            playlist_name = f"歌单 {playlist_id[:12]}"

        if not songs:
            return {
                "name": playlist_name,
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "truncated": False,
                "items": [],
                "message": "歌单内没有可下载的歌曲",
            }

        # 目标目录对所有曲目都是一样的：扁平落盘，整理交给 MP 本体
        target_dir = self._resolve_download_dir()
        quality = quality or self._quality

        workers = max(1, min(int(concurrency or 3), 8))
        results: list[Optional[dict]] = [None] * len(songs)
        cursor = threading.Lock()
        next_index = 0

        def consume() -> None:
            """工作线程主体：从游标里抢下一个序号并下载，取完即退出。"""
            nonlocal next_index
            while True:
                with cursor:
                    if next_index >= len(songs):
                        return
                    position = next_index
                    next_index += 1
                results[position] = self._download_playlist_item(
                    songs[position], position + 1, target_dir, quality, skip_existing
                )

        threads = [threading.Thread(target=consume, daemon=True) for _ in range(min(workers, len(songs)))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        items = [item for item in results if isinstance(item, dict)]
        succeeded = [item for item in items if item["status"] == "ok"]
        skipped = [item for item in items if item["status"] == "skipped"]
        failed = [item for item in items if item["status"] == "failed"]

        return {
            "name": playlist_name,
            "source": payload.get("source") or source,
            "dir": str(target_dir),
            "quality": quality,
            "total": len(songs),
            "playlist_total": payload.get("total", len(songs)),
            "truncated": bool(payload.get("truncated")),
            "limited": bool(limit and limit > 0 and payload.get("total", 0) > limit),
            "success": len(succeeded),
            "failed": len(failed),
            "skipped": len(skipped),
            "items": items,
        }

    def _download_playlist_item(
        self,
        song: dict,
        index: int,
        target_dir: Path,
        quality: str,
        skip_existing: bool,
    ) -> dict:
        """下载歌单里的单首，失败不抛出，转成一条明细记录。"""
        name = str(song.get("name") or "未知歌曲")
        singer = str(song.get("singer") or "")
        if not self._use_server_cache:
            try:
                remain = self._remaining_playlist_file(song, target_dir)
                if remain is not None and skip_existing:
                    return {
                        "index": index, "name": name, "singer": singer,
                        "status": "skipped", "message": "目标目录已有同名文件",
                    }
            except Exception as err:  # noqa: BLE001
                logger.warn(f"歌单下载去重检查失败（{name}）：{err}")

        try:
            message = self._download_song(song, quality)
        except LxServerError as err:
            logger.warn(f"歌单第 {index} 首《{name}》下载失败：{err}")
            return {"index": index, "name": name, "singer": singer, "status": "failed", "message": str(err)}
        except Exception as err:  # noqa: BLE001
            logger.error(f"歌单第 {index} 首《{name}》下载异常：{err}")
            return {"index": index, "name": name, "singer": singer, "status": "failed", "message": str(err)}

        return {"index": index, "name": name, "singer": singer, "status": "ok", "message": message.splitlines()[0]}

    def _remaining_playlist_file(self, song: dict, target_dir: Path) -> Optional[Path]:
        """按当前文件名模板推算目标文件是否已存在，用于整单下载去重。

        扩展名由响应内容决定，这里无法预知，因此用「主干名 + 任意音频后缀」
        匹配。名称模板带 songmid 时不会碰撞；若不带，同一首歌重复下载会被
        判定为已存在——这正是整单下载想要的效果（幂等）。
        """
        base_name = LxDownloader.build_filename(song, self._name_template)
        if not base_name or base_name == "unknown":
            return None
        for suffix in (".mp3", ".flac", ".m4a", ".ogg", ".ape", ".wav", ".dts"):
            candidate = target_dir / f"{base_name}{suffix}"
            if candidate.exists():
                return candidate
        return None

    # ------------------------------------------------------------------ #
    #                            定时任务                                  #
    # ------------------------------------------------------------------ #
    def get_service(self) -> list[dict]:
        """插件启用时每天校验一次凭据，尽早暴露 token 失效。"""
        if not self.get_state():
            return []
        return [
            {
                "id": "LxMusicDownloader.Verify",
                "name": "LX 服务端凭据校验",
                "trigger": CronTrigger.from_crontab("0 9 * * *"),
                "func": self._verify_credential,
                "kwargs": {},
            }
        ]

    def _verify_credential(self) -> None:
        """校验服务端凭据，失败时发出通知。"""
        try:
            data = self.get_client().verify()
        except LxServerError as err:
            logger.warn(f"LX 服务端凭据校验失败：{err}")
            self.post_message(
                mtype=NotificationType.Plugin,
                title="LX 音源下载",
                text=f"服务端凭据校验失败，请检查配置：{err}",
            )
            return
        if not data.get("valid"):
            logger.warn("LX 服务端 token 无效，将在下次调用时重新登录")
            self._client = None

    # ------------------------------------------------------------------ #
    #                              页面                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        """已构建联邦产物时使用 Vue 远程组件，否则回退 Vuetify 表单。"""
        dist_path = Path(__file__).parent / "dist" / "assets"
        if (dist_path / "remoteEntry.js").exists():
            return "vue", "dist/assets"
        return "vuetify", None

    def get_sidebar_nav(self) -> List[Dict[str, Any]]:
        """声明主界面左侧导航栏入口，仅在 Vue 模式、已启用且未关闭侧栏时生效。"""
        if not self.get_state():
            return []
        if self.get_render_mode()[0] != "vue":
            return []
        if not self._sidebar_enabled:
            return []
        return [{
            "nav_key": "main",
            "title": "LX 音源下载",
            "icon": "mdi-music-box-multiple",
            "section": "organize",
            "permission": "manage",
            "order": 61,
        }]

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """返回配置页面和默认配置。"""
        source_items = [{"title": label, "value": key} for key, label in SUPPORTED_SOURCES.items()]
        quality_items = [
            {"title": "128k", "value": "128k"},
            {"title": "320k", "value": "320k"},
            {"title": "flac", "value": "flac"},
            {"title": "flac24bit", "value": "flac24bit"},
            {"title": "hires", "value": "hires"},
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {"model": "source", "label": "音源", "items": source_items},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {"model": "quality", "label": "下载音质", "items": quality_items},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "host",
                            "label": "LX 服务端地址",
                            "placeholder": "https://music.example.com 或 http://127.0.0.1:23332",
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {"model": "username", "label": "用户名"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "password",
                                            "label": "密码",
                                            "type": "password",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "token",
                                            "label": "持久化 Token",
                                            "placeholder": "填了可免密码；用户名仍需填写",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "download_path",
                            "label": "下载位置",
                            "placeholder": "/media/music，留空使用插件数据目录",
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "name_template",
                                            "label": "文件名模板",
                                            "placeholder": "支持 {name} {singer} {album} {source} {songmid}",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_results",
                                            "label": "搜索结果数",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "playlist_concurrency",
                                            "label": "歌单下载并发",
                                            "type": "number",
                                            "placeholder": "1-8，默认 3",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "subdir_by_artist", "label": "按歌手分目录"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "save_cover", "label": "保存封面"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "embed_tag", "label": "注入 ID3 标签"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "embed_lyric", "label": "嵌入歌词"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "use_server_cache", "label": "下到服务端缓存"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "sidebar_enabled", "label": "显示侧栏入口"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "split_artists",
                                            "label": "拆分多歌手标签",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "host": "http://127.0.0.1:23332",
            "username": "",
            "password": "",
            "token": "",
            "source": "kw",
            "quality": "320k",
            "download_path": "",
            "name_template": "{name} - {singer}",
            "max_results": MAX_CANDIDATES,
            "playlist_concurrency": 3,
            "subdir_by_artist": True,
            "save_cover": False,
            "embed_tag": True,
            "embed_lyric": True,
            "use_server_cache": False,
            "sidebar_enabled": True,
            "split_artists": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页，展示连接状态与下载位置。

        Vue 模式下（已构建联邦产物）详情页由远程组件渲染，返回空列表；
        同时避免在每次打开详情页时同步调用服务端（旧实现的阻塞点）。
        """
        if self.get_render_mode()[0] == "vue":
            return []

        try:
            client = self.get_client()
            info = client.verify()
            if info.get("valid"):
                state = f"鉴权正常（用户 {info.get('username')}）"
            elif client.authenticated:
                state = "Token 无效"
            else:
                state = "未配置凭据，将以公开用户访问（可能无法解析直链）"
        except LxServerError as err:
            state = f"连接失败：{err}"
        except Exception as err:  # noqa: BLE001
            state = f"连接异常：{err}"

        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "远程命令：/lx_search 歌曲名｜/lx_download 序号｜/lx_stats",
                },
            },
            {
                "component": "VTable",
                "props": {
                    "headers": [
                        {"title": "项目", "key": "key", "sortable": False},
                        {"title": "值", "key": "value", "sortable": False},
                    ],
                    "items": [
                        {"key": "服务端", "value": getattr(self, "_host", "-") or "未配置"},
                        {"key": "音源", "value": SUPPORTED_SOURCES.get(getattr(self, "_source", ""), "-")},
                        {"key": "音质", "value": getattr(self, "_quality", "-")},
                        {"key": "下载位置", "value": str(self._resolve_download_dir())},
                        {"key": "状态", "value": state},
                    ],
                    "density": "compact",
                },
            },
        ]

    def get_api(self) -> list[dict[str, Any]]:
        """注册前端与外部调用所需的接口，全部走 bear 认证。"""
        return [
            {
                "path": "/overview",
                "endpoint": self.api_overview,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "运行状态总览",
            },
            {
                "path": "/verify",
                "endpoint": self.api_verify,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "校验服务端凭据",
            },
            {
                "path": "/search",
                "endpoint": self.api_search,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "搜索歌曲",
            },
            {
                "path": "/resolve",
                "endpoint": self.api_resolve,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "解析播放直链",
            },
            {
                "path": "/download",
                "endpoint": self.api_download,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "下载歌曲（可传完整 song 或 keyword）",
            },
            {
                "path": "/playlist/list",
                "endpoint": self.api_playlist_list,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "浏览歌单（标签/关键词）",
            },
            {
                "path": "/playlist/tags",
                "endpoint": self.api_playlist_tags,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "歌单标签与排序方式",
            },
            {
                "path": "/playlist/detail",
                "endpoint": self.api_playlist_detail,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "歌单详情（含曲目列表）",
            },
            {
                "path": "/playlist/download",
                "endpoint": self.api_playlist_download,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "整单下载歌单",
            },
            {
                "path": "/stats",
                "endpoint": self.api_stats,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "服务端缓存统计",
            },
        ]

    @staticmethod
    def _fail(err: Exception, data: Any = None) -> dict[str, Any]:
        """统一的失败信封，避免异常穿透成 500。"""
        return {"success": False, "message": str(err), "data": data}

    def _clamp_limit(self, limit: Any) -> int:
        """把前端传入的条数钳制到 1..50，防止超大 pages 请求。"""
        try:
            return max(1, min(int(limit or self._max_results), 50))
        except (TypeError, ValueError):
            return self._max_results

    async def api_overview(self) -> dict[str, Any]:
        """返回前端总览：配置摘要 + 凭据校验状态（校验失败不抛错）。"""
        try:
            client = self.get_client()
            info = client.verify()
            if info.get("valid"):
                auth_state = "ok"
                auth_text = f"鉴权正常（用户 {info.get('username')}）"
            elif getattr(client, "token", ""):
                auth_state, auth_text = "invalid", "Token 无效或未生效"
            else:
                auth_state, auth_text = "anonymous", "未配置凭据，将以公开用户访问（无法解析直链）"
        except LxServerError as err:
            auth_state, auth_text = "error", f"连接失败：{err}"
        except Exception as err:  # noqa: BLE001
            auth_state, auth_text = "error", f"连接异常：{err}"

        return {
            "success": True,
            "data": {
                "enabled": bool(self._enabled),
                "host": getattr(self, "_host", "") or "",
                "source": getattr(self, "_source", ""),
                "source_name": SUPPORTED_SOURCES.get(getattr(self, "_source", ""), "-"),
                "quality": getattr(self, "_quality", ""),
                "download_dir": str(self._resolve_download_dir()),
                "max_results": getattr(self, "_max_results", MAX_CANDIDATES),
                "playlist_concurrency": getattr(self, "_playlist_concurrency", 3),
                "sidebar_enabled": bool(getattr(self, "_sidebar_enabled", True)),
                "auth_state": auth_state,
                "auth_text": auth_text,
            },
        }

    async def api_verify(self) -> dict[str, Any]:
        """校验服务端凭据。"""
        try:
            return {"success": True, "data": self.get_client().verify()}
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err, {"error": repr(err)})

    async def api_search(self, keyword: str = "", limit: int = MAX_CANDIDATES,
                         source: str = "") -> dict[str, Any]:
        """按关键词搜索歌曲。"""
        if not keyword:
            return {"success": False, "message": "缺少关键词", "data": []}
        try:
            songs = self.get_client().search(
                keyword, source=source or self._source, limit=self._clamp_limit(limit)
            )
        except LxServerError as err:
            return {"success": False, "message": str(err), "data": []}
        except Exception as err:  # noqa: BLE001
            return {"success": False, "message": str(err), "data": []}
        return {"success": True, "data": songs}

    async def api_resolve(self, payload: dict = Body(default=None)) -> dict[str, Any]:
        """解析播放直链（不落盘），供前端确认实际音质。"""
        data = payload or {}
        song = data.get("song")
        if not isinstance(song, dict) or not song:
            return self._fail(ValueError("缺少 song 参数"))
        try:
            client = self.get_client()
            quality = self._pick_quality(song, str(data.get("quality") or self._quality))
            info = client.music_url(song, quality)
            if not info.get("url"):
                return self._fail(LxServerError(info.get("error") or "未获取到播放直链"))
            return {"success": True, "data": {
                "url": info.get("url"),
                "type": info.get("type") or info.get("quality") or quality,
                "source_name": info.get("sourceName") or "",
            }}
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err)

    async def api_download(self, payload: dict = Body(default=None),
                           keyword: str = "") -> dict[str, Any]:
        """下载歌曲：优先用前端回传的完整 song，避免二次搜索选错版本。"""
        data = payload or {}
        song = data.get("song")
        kw = str(data.get("keyword") or keyword or "").strip()
        try:
            if not isinstance(song, dict) or not song:
                if not kw:
                    return {"success": False, "message": "缺少 song 或 keyword 参数"}
                songs = self.get_client().search(kw, source=self._source, limit=1)
                if not songs:
                    return {"success": False, "message": f"「{self._source}」没有搜索到与「{kw}」相关的歌曲"}
                song = songs[0]
            message = self._download_song(song, data.get("quality"))
            return {"success": True, "message": message, "data": {
                "name": song.get("name"), "singer": song.get("singer"),
            }}
        except LxServerError as err:
            return {"success": False, "message": str(err)}
        except Exception as err:  # noqa: BLE001
            return {"success": False, "message": str(err)}

    async def api_stats(self) -> dict[str, Any]:
        """返回服务端缓存统计。"""
        try:
            return {"success": True, "data": self.get_client().cache_stats()}
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err)

    # ------------------------------------------------------------------ #
    #                            歌单接口                                  #
    # ------------------------------------------------------------------ #
    async def api_playlist_list(self, keyword: str = "", source: str = "",
                                tag_id: str = "", sort_id: str = "hot",
                                page: int = 1) -> dict[str, Any]:
        """浏览歌单：给了 keyword 就搜索，否则按标签/热门列表取。"""
        src = source or self._source or "wy"
        try:
            client = self.get_client()
            if keyword.strip():
                rows = client.songlist_search(keyword.strip(), source=src, page=page)
            else:
                rows = client.songlist_list(source=src, tag_id=tag_id, sort_id=sort_id, page=page)
            return {"success": True, "data": rows}
        except LxServerError as err:
            return {"success": False, "message": str(err), "data": []}
        except Exception as err:  # noqa: BLE001
            return {"success": False, "message": str(err), "data": []}

    async def api_playlist_tags(self, source: str = "") -> dict[str, Any]:
        """歌单标签与排序方式。"""
        try:
            return {"success": True, "data": self.get_client().songlist_tags(source=source or self._source or "wy")}
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err)

    async def api_playlist_detail(self, playlist_id: str = "", id: str = "",
                                  source: str = "", max_songs: int = 1000) -> dict[str, Any]:
        """取歌单详情（含全量曲目，自动翻页）。"""
        target = (playlist_id or id or "").strip()
        if not target:
            return {"success": False, "message": "缺少歌单 ID 或链接"}
        try:
            limit = max(1, min(int(max_songs or 1000), 5000))
        except (TypeError, ValueError):
            limit = 1000
        try:
            return {"success": True, "data": self.fetch_playlist(target, source=source, max_songs=limit)}
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err)

    async def api_playlist_download(self, payload: dict = Body(default=None)) -> dict[str, Any]:
        """整单下载歌单。

        入参：
        - ``playlist_id`` 歌单链接或 ID（必填）
        - ``songs``       前端已拿到的曲目数组；不传则服务端重新拉取整个歌单
        - ``source``      音源平台
        - ``quality``     目标音质，缺省用插件配置
        - ``limit``       只下载前 N 首，0 表示不限
        - ``concurrency`` 并发数，缺省用插件配置
        - ``skip_existing`` 是否跳过目标目录已有同名文件，默认 True
        """
        data = payload or {}
        playlist_id = str(data.get("playlist_id") or data.get("id") or "").strip()
        if not playlist_id:
            return {"success": False, "message": "缺少 playlist_id 参数"}
        try:
            concurrency = data.get("concurrency")
            concurrency = self._playlist_concurrency if concurrency in (None, "") else int(concurrency)
        except (TypeError, ValueError):
            concurrency = self._playlist_concurrency
        try:
            limit = int(data.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0

        try:
            result = self.download_playlist(
                playlist_id,
                source=str(data.get("source") or ""),
                quality=data.get("quality"),
                concurrency=concurrency,
                skip_existing=bool(data.get("skip_existing", True)),
                limit=max(0, limit),
            )
        except LxServerError as err:
            return self._fail(err)
        except Exception as err:  # noqa: BLE001
            return self._fail(err)

        summary = (
            f"《{result['name']}》：成功 {result['success']}｜"
            f"跳过 {result['skipped']}（已存在）｜失败 {result['failed']}｜共 {result['total']} 首。\n"
            f"目录：{result.get('dir')}"
        )
        return {"success": True, "message": summary, "data": result}
