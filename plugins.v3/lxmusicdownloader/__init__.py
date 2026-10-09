# -*- coding: utf-8 -*-
"""LX 音源下载插件。

通过自建 LX Sync Server 的 HTTP API 完成歌曲搜索、歌单浏览、直链解析与下载，
支持自定义下载位置，并通过远程命令 `/lx_search`、`/lx_download`、`/lx_playlist`、
`/lx_stats` 响应请求。

插件不再在本地运行洛雪自定义源 JavaScript，音源能力由服务端提供，
因此运行环境不需要 Node.js。

另可选开启「音乐识别回写」：注册 ``ChainEventType.MusicMediaRecognize`` 链式事件，
在原生识别拿不到远端身份时用 LX 的解析结果补齐身份与专辑级事实（专辑类型、曲序、
发行日期），从而让本体的自动分类与重命名模板生效。详细约束见 ``recognizer.py``。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app.schemas.types import ChainEventType, EventType, MediaSource, MediaType, MessageType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.media import normalize_media_source
from app.sdk.plugin import _PluginBase

from .downloader import LxDownloader, split_artists
from .lxserver import SUPPORTED_SOURCES, LxServerClient, LxServerError
from .recognizer import ALBUM_SONG_SOURCES, MEDIA_SOURCE, LxMusicRecognizer

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
    plugin_version = "3.4.0"
    plugin_author = "157888390"
    author_url = "https://github.com/157888390"
    plugin_config_prefix = "lxmusicdownloader_"
    plugin_order = 61
    auth_level = 1

    _enabled = False
    _sidebar_enabled = True          # 是否在主界面左侧导航栏显示入口
    _recognize_enabled = False       # 是否启用音乐识别（链式事件回写 + 注册为宿主数据源）
    _client: Optional[LxServerClient] = None
    _lock: Optional[threading.Lock] = None
    # 识别器持有命中缓存，必须按运行实例隔离，所以类属性保持 None，
    # 保证首次 init_plugin 一定落到实例属性上（V3 要求实例间不共享可变状态）
    _recognizer: Optional[LxMusicRecognizer] = None
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
        # 歌词两个开关独立：embed_lyric 写入 USLT 标签、download_lyric 落 .lrc 文件。
        # 均默认开启（对齐 lxserver 同步下载的面板默认值）。
        self._embed_lyric = bool(config.get("embed_lyric", True))
        self._download_lyric = bool(config.get("download_lyric", True))
        self._split_artists = bool(config.get("split_artists", True))
        self._sidebar_enabled = bool(config.get("sidebar_enabled", True))
        self._recognize_enabled = bool(config.get("recognize_enabled"))
        # 识别平台与下载平台分开配置：专辑曲目接口只有 tx / wy 实现，
        # 而默认下载源是 kw，两者混用会让曲序和发行日期永远取不到
        self._recognize_source = str(config.get("recognize_source") or "wy").strip().lower() or "wy"
        try:
            self._recognize_timeout = max(
                1.0, min(float(config.get("recognize_timeout") or 8), 30.0)
            )
        except (TypeError, ValueError):
            self._recognize_timeout = 8.0
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
        # 识别器一并重建，丢弃上一份命中缓存（换平台后旧命中不可复用）
        self._recognizer = LxMusicRecognizer(
            client_factory=self.get_client,
            source=self._recognize_source,
            limit=self._max_results,
            timeout=self._recognize_timeout,
        )

        if self._enabled:
            try:
                self.get_client()
            except LxServerError as err:
                logger.warn(f"LX 服务端初始化失败，将在首次使用时重试：{err}")

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return bool(self._enabled)

    def stop_service(self) -> None:
        """释放后台资源，可重复调用。

        这里**故意不调用** ``eventmanager.disable_event_handler``：宿主
        ``PluginLifecycle`` 会在停止时按"处理器类"统一禁用、重新启用时按类恢复，
        而手工禁用记的是"处理器标识"，两份账本不通 —— 手工关掉之后类级恢复清不掉
        这个标识，表现为热重载后事件静默不派发。链式事件处理器的生效开关统一由
        ``get_state()``（宿主据此决定是否启用本类的 handler）与函数体内的配置判断控制。
        """
        self._enabled = False
        self._client = None
        self._last_results = {}
        if self._recognizer:
            self._recognizer.clear()
        self._recognizer = None

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
            result = client.cache_download(
                song,
                play_url,
                real_quality,
                cache_lyric=self._download_lyric,
                embed_lyric=self._embed_lyric,
            )
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
        # 发行类型也趁落盘前写进标签：本地标签齐全时整理不再向在线来源确认身份，
        # 专辑类型只可能来自标签，缺了它就只能落进「未分类」。
        album_type = self._album_type_for(song)

        with client.open_download(
            play_url, base_name, song, embed_tag=self._embed_tag, embed_lyric=self._embed_lyric
        ) as response:
            # 下载接口的错误也是 JSON 体，必须在落盘前判定，否则会把错误页写成音频文件
            if response.status_code >= 400:
                detail = self._read_error_body(response)
                raise LxServerError(f"代理下载失败：HTTP {response.status_code} {detail}")
            saved, size = self._downloader.save_stream(
                response, target_dir, base_name, quality, artists, album_type
            )

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

        # 下载 .lrc 歌词文件：代理下载只返回音频流，歌词要单独拉取后落盘到同目录。
        # 与 embed_lyric（写 USLT 标签）互不影响，可独立开关。
        if self._download_lyric:
            try:
                lyric_text = client.fetch_lyric(song)
                if lyric_text:
                    lyric_path = self._downloader.save_lyric(saved, lyric_text)
                    if lyric_path:
                        lines.append(f"歌词：{lyric_path.name}")
            except Exception as err:  # noqa: BLE001
                logger.warn(f"歌词下载失败：{err}")

        return "\n".join(lines)

    def _album_type_for(self, song: dict) -> Optional[str]:
        """推断本次下载歌曲的发行类型；不需要时返回 None。

        挂在 ``embed_tag`` 下而不是单独做一个开关：发行类型是"注入标签"的一部分，
        而关掉 ``embed_tag`` 时文件本来就不带 title / artist / album，整理必然走在线
        识别通道，此时补写 releasetype 既没用也白搭一次查询。

        复用 ``_recognizer`` 的实例缓存：整单下载同一张专辑时只查一次。
        取不到确凿曲目数时返回 None（不写标签），详见 ``recognizer.album_type_for``。
        """
        if not self._embed_tag or self._recognizer is None:
            return None
        return self._recognizer.album_type_for(song)

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
            mtype=MessageType.Plugin,
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
        songs: Optional[list[dict]] = None,
        playlist_name: str = "",
    ) -> dict:
        """整单下载：并发解析 + 落盘，逐首给出成功/失败明细。

        ``songs`` 非空时只下载前端勾选的这些曲目（不再重新拉取整张歌单）；
        否则按 ``limit``（>0 取前 N 首）从服务端拉取整单。并发默认 3，与服务端
        下载队列的默认并发保持一致，避免把上游打爆。
        """
        truncated = False
        playlist_total: Optional[int] = None
        resolved_source = source
        if songs:
            selected = [s for s in songs if isinstance(s, dict)]
            name = playlist_name or f"歌单 {playlist_id[:12]}"
        else:
            payload = self.fetch_playlist(playlist_id, source=source, max_songs=max_songs)
            selected = [s for s in payload["songs"] if isinstance(s, dict)]
            if limit and limit > 0:
                selected = selected[:limit]
            info = payload.get("info") or {}
            name = str(info.get("name") or "").strip() or f"歌单 {playlist_id[:12]}"
            resolved_source = payload.get("source") or source
            truncated = bool(payload.get("truncated"))
            playlist_total = payload.get("total", len(selected))

        if not selected:
            return {
                "name": name,
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "truncated": truncated,
                "items": [],
                "message": "歌单内没有可下载的歌曲",
            }

        # 目标目录对所有曲目都是一样的：扁平落盘，整理交给 MP 本体
        target_dir = self._resolve_download_dir()
        quality = quality or self._quality

        workers = max(1, min(int(concurrency or 3), 8))
        results: list[Optional[dict]] = [None] * len(selected)
        cursor = threading.Lock()
        next_index = 0

        def consume() -> None:
            """工作线程主体：从游标里抢下一个序号并下载，取完即退出。"""
            nonlocal next_index
            while True:
                with cursor:
                    if next_index >= len(selected):
                        return
                    position = next_index
                    next_index += 1
                results[position] = self._download_playlist_item(
                    selected[position], position + 1, target_dir, quality, skip_existing
                )

        threads = [threading.Thread(target=consume, daemon=True) for _ in range(min(workers, len(selected)))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        items = [item for item in results if isinstance(item, dict)]
        succeeded = [item for item in items if item["status"] == "ok"]
        skipped = [item for item in items if item["status"] == "skipped"]
        failed = [item for item in items if item["status"] == "failed"]

        return {
            "name": name,
            "source": resolved_source,
            "dir": str(target_dir),
            "quality": quality,
            "total": len(selected),
            "playlist_total": playlist_total if playlist_total is not None else len(selected),
            "truncated": truncated,
            "limited": bool(limit and limit > 0 and (playlist_total or 0) > limit),
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
    #                          音乐识别回写                                #
    # ------------------------------------------------------------------ #
    @eventmanager.register(ChainEventType.MusicMediaRecognize)
    def handle_music_media_recognize(self, event: Event) -> None:
        """用 LX 服务端的解析结果补齐本体的音乐识别身份。

        触发时机：原生识别（标签 / 文件名 / 目录名 + 在线源）没能给出带远端身份的
        候选时，宿主广播 ``music.media.recognize``，把已知要素交给插件去匹配。
        插件回写了带身份的结果后宿主会短路后续识别步骤，所以不回写就等于"没识别出来"。

        ⚠️ 两条实测约束，改动前务必先读：

        1. **回写的事实必须与本地证据一致。** 否则 ``app/chain/media/path.py`` 的
           ``_music_info_matches_text_evidence`` 会把整条结果丢弃（只留一行 warning，
           表现为"插件明明返回了却没生效"）。所以 ``recognizer`` 里对 title / artists /
           year / album 一律回显载荷中的本地值。
        2. **handler 是同步派发的**（``dispatch_chain`` → ``invoke_sync``），会直接阻塞
           识别线程。所以网络请求全部带短超时，并靠识别器内的 TTL 缓存压掉重复查询。

        处理器返回值不被使用，回写方式就是改 ``event.event_data`` 本身——dispatcher
        拿到的是同一个 dict 对象（``Event.__init__`` 直接持有入参引用）。
        """
        if not (self._enabled and self._recognize_enabled):
            return
        recognizer = self._recognizer
        if recognizer is None:
            return
        payload = event.event_data
        if not isinstance(payload, dict):
            return

        info = recognizer.recognize(payload)
        if not info:
            return
        payload["mediainfo"] = info

    # ------------------------------------------------------------------ #
    #                      音乐数据源注册（模块通道）                        #
    # ------------------------------------------------------------------ #
    # 与上面的链式事件是两条独立通道，共用 ``recognizer`` 里的同一份匹配逻辑：
    #   链式事件 —— 宿主原生识别拿不到远端身份后才反问插件（我们是被动的兜底）；
    #   模块通道 —— 插件注册成宿主的一等数据源，用户在来源选择器里选中它就直接被调用。
    # 官方文档见 docs/faq/19-register-media-source.md。
    def get_media_source(self) -> list[dict[str, Any]]:
        """向宿主声明 lxmusic 来源，使其出现在前端的来源选择器里。

        ``media_types`` 必须含 ``MediaType.MUSIC``，宿主前端据此把它放进音乐类选项。
        宿主只在插件启用时才收集（``projection.media_sources`` 会先查 ``get_state()``），
        停用后来源自动撤销，但历史数据保留原有 ``media_source`` / ``media_id``。
        """
        if not self._recognize_enabled:
            return []
        return [
            {
                "name": "LX 音乐",
                "media_source": MEDIA_SOURCE,
                "media_types": [MediaType.MUSIC],
            }
        ]

    def get_module(self) -> dict[str, Any]:
        """把识别能力注册进宿主的模块调度器。

        调度顺序是「先插件、返回非空即短路」（``ModuleInvocationDispatcher._dispatch``），
        所以每个方法都必须先确认 ``media_source`` 是本插件来源，不匹配一律返回 None，
        否则会拦截其它来源的请求。

        ``async_recognize_media`` 与同步版本指向同一个函数：调度器对同步函数会自动
        放进线程池执行（``dispatcher._async_call``），因此不需要两份实现。
        音乐域的通用端口 ``MusicMetadataSourceChain`` 认的就是这几个方法名。
        """
        if not (self._enabled and self._recognize_enabled):
            return {}
        return {
            "recognize_media": self.recognize_media,
            "async_recognize_media": self.recognize_media,
            "search_music": self.search_music,
            "music_album": self.music_album,
        }

    def _is_lx_source(self, media_source: Any) -> bool:
        """判断请求来源是否为本插件来源（不匹配时调用方必须原样返回 None / 空列表）。"""
        normalized = normalize_media_source(media_source)
        return normalized is not None and normalized.value == MEDIA_SOURCE

    def _music_payload(self, meta: Any, music_type: Any) -> dict[str, Any]:
        """把宿主的 ``MetaMusic`` 归一成与链式事件**完全相同**的要素载荷。

        两条通道共用 ``recognizer``，载荷形状必须一致，否则同一首歌会因通道不同
        得出不同结果。字段清单对齐 ``_recognition._media_recognize_plugin_payload``。
        """
        return {
            "title": getattr(meta, "title", None),
            "artists": list(getattr(meta, "artists", None) or []),
            "album": getattr(meta, "album", None),
            "year": getattr(meta, "year", None),
            "isrc": getattr(meta, "isrc", None),
            "music_type": music_type or getattr(meta, "music_type", None) or "recording",
        }

    def recognize_media(
        self,
        meta: Any = None,
        mtype: Any = None,
        media_source: Any = None,
        media_id: Optional[str] = None,
        cache: bool = True,
        music_type: Any = None,
        **kwargs: Any,
    ) -> Optional[dict]:
        """按身份或标题要素识别一首歌。

        带 ``media_id`` 时优先走身份索引回放（搜索候选被选中、重新识别、手动刮削
        都走这条）；没有身份时退回按要素打分匹配，与链式事件通道共用同一份逻辑。
        """
        if not self._is_lx_source(media_source):
            return None
        recognizer = self._recognizer
        if recognizer is None:
            return None

        payload = self._music_payload(meta, music_type)
        if media_id:
            return recognizer.recognize_by_id(str(media_id), payload)
        if not payload.get("title"):
            return None
        return recognizer.recognize(payload)

    def search_music(
        self,
        meta: Any = None,
        limit: int = 20,
        media_source: Any = None,
        **kwargs: Any,
    ) -> list[dict]:
        """在统一媒体搜索里返回本来源的候选列表。

        宿主按 ``ORDERED_LIST_MERGE`` 聚合，多个来源的候选合并成一份；返回的每条
        都必须带上本来源的 ``media_source`` + ``media_id``，否则会被来源链过滤掉。
        """
        if not self._is_lx_source(media_source):
            return []
        recognizer = self._recognizer
        if recognizer is None:
            return []

        payload = self._music_payload(meta, None)
        if not payload.get("title"):
            return []
        try:
            return recognizer.search_candidates(payload, limit)
        except LxServerError as err:
            logger.warn(f"LX 搜索失败：{err}")
            return []
        except Exception as err:  # noqa: BLE001  搜索失败不能让整个搜索接口 500
            logger.warn(f"LX 搜索异常：{err}")
            return []

    def music_album(self, media_source: Any = None, media_id: Optional[str] = None,
                    **kwargs: Any) -> Optional[dict]:
        """按专辑 ``media_id`` 返回专辑详情（含曲目表）。

        专辑级身份**不依赖身份索引**：曲目接口直接用 albumId 查，重启后依然可用。
        """
        if not self._is_lx_source(media_source):
            return None
        recognizer = self._recognizer
        if recognizer is None or not media_id:
            return None
        try:
            return recognizer.album_detail(str(media_id))
        except LxServerError as err:
            logger.debug(f"LX 专辑详情查询失败：{err}")
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
                mtype=MessageType.Plugin,
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
        # 识别平台单独排序：只有 tx / wy 实现了专辑曲目接口，能顺带拿到曲序与发行日期，
        # 其余平台只能确认歌曲身份，因此把可用的排在前面并标注出来
        recognize_items = [
            {"title": f"{label}（可读专辑曲目）", "value": key}
            for key, label in SUPPORTED_SOURCES.items() if key in ALBUM_SONG_SOURCES
        ] + [
            {"title": label, "value": key}
            for key, label in SUPPORTED_SOURCES.items() if key not in ALBUM_SONG_SOURCES
        ]
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
                                        "props": {
                                            "model": "recognize_enabled",
                                            "label": "音乐识别（注册为数据源）",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "recognize_source",
                                            "label": "识别平台",
                                            "items": recognize_items,
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
                                            "model": "recognize_timeout",
                                            "label": "识别超时（秒）",
                                            "type": "number",
                                            "placeholder": "1-30，默认 8",
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
                                        "props": {"model": "embed_lyric", "label": "嵌入 USLT 标签"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "download_lyric", "label": "下载歌词文件"},
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
            "download_lyric": True,
            "use_server_cache": False,
            "sidebar_enabled": True,
            "split_artists": True,
            "recognize_enabled": False,
            "recognize_source": "wy",
            "recognize_timeout": 8,
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
                songs = await asyncio.to_thread(
                    self.get_client().search, kw, source=self._source, limit=1
                )
                if not songs:
                    return {"success": False, "message": f"「{self._source}」没有搜索到与「{kw}」相关的歌曲"}
                song = songs[0]
            # 同步下载是长流程（含大文件写盘），放线程池避免阻塞事件循环
            message = await asyncio.to_thread(self._download_song, song, data.get("quality"))
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
            # 整单下载内部并发 + join，也是长流程，放线程池避免阻塞事件循环
            result = await asyncio.to_thread(
                self.download_playlist,
                playlist_id,
                source=str(data.get("source") or ""),
                quality=data.get("quality"),
                concurrency=concurrency,
                skip_existing=bool(data.get("skip_existing", True)),
                limit=max(0, limit),
                songs=data.get("songs"),
                playlist_name=str(data.get("name") or ""),
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
