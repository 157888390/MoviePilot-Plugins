# -*- coding: utf-8 -*-
"""把 LX 服务端的解析结果翻译成 MoviePilot 的音乐识别回写负载。

本模块只做纯逻辑（打分、筛选、组装），HTTP 交互全部委托给 :class:`LxServerClient`，
因此可以脱离运行环境单独测试。

插件有**两条**把结果交给宿主的通道，两条都走本模块，行为必须一致：

- **链式事件**（``ChainEventType.MusicMediaRecognize``）：宿主原生识别拿不到远端身份时反问插件。
- **模块通道**（``get_module()`` 注册 ``recognize_media`` / ``search_music`` / ``music_album``）：
  插件把自己注册成宿主的音乐数据源，宿主直接调用，不再受"原生失败才轮到我们"的限制。
  两者载荷形状刻意保持一致，见 ``__init__.py`` 的 ``_music_payload``。

改动前请先读完下面四条实测结论，它们各自对应一个"踩过才知道"的坑：

1. **回写的事实必须与本地证据一致。** ``app/chain/media/path.py`` 的
   ``_music_info_matches_text_evidence`` 会拿插件结果和本地标签比对，``year``
   与本地不一致时**整条结果被丢弃**（只留一行 warning，表现为"插件明明返回了却没生效"）。
   所以这里对 ``title`` / ``artists`` / ``year`` / ``album`` 一律**优先回显事件载荷里的
   本地值**，只有本地缺该字段时才用 LX 的值补齐。真正的新增信息是
   ``media_source`` / ``media_id`` / ``album_type`` / ``track_number``。
2. **搜索结果里的 ``albumId`` 在顶层，不在 ``meta`` 里**，而插件下载链路读的是 ``meta``。
   两边取值方式不同，别混。
3. **``getAlbumSongs`` 只有 tx / wy 两个平台实现**，kw / kg / mg 没有 extendDetail，
   调用会直接 500。所以专辑级信息（曲序、发行日期）走"能取到才取"的降级路径。
4. **处理函数是同步派发的**（``dispatch_chain`` → ``invoke_sync``），会阻塞识别管线。
   因此这里所有请求都带短超时，并用 TTL 缓存压掉同一批次里的重复查询。

此外下载落盘路径也用本模块的 :meth:`LxMusicRecognizer.album_type_for` 推断发行类型，
在写完文件前把 ``releasetype`` 补进标签（见 ``downloader.apply_album_type``）——整理
只要本地标签齐全就不会再向在线来源确认身份，那时专辑类型只可能来自标签本身。
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Iterable, Optional

from app.sdk.logging import logger

from .downloader import split_artists
from .lxserver import LxServerClient, LxServerError

# 回写用的媒体源标识。MP 的 ``MediaSource._missing_`` 会为符合
# ``^[a-z][a-z0-9._-]{0,63}$`` 的值动态创建枚举成员，所以自定义源不需要宿主白名单。
MEDIA_SOURCE = "lxmusic"

# 只有这两个平台实现了 extendDetail.getAlbumSongs，其余平台调用会 500
ALBUM_SONG_SOURCES = frozenset({"tx", "wy"})

# 带括号的补充信息（live / remix / 影视剧名 / 年份），用于区分"同名不同版本"
_BRACKET = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")
# 比较时忽略的空白与常见连接符
_JOINER = re.compile(r"[\s\-_·・'\"“”]+")

# 打分权重
_TITLE_EXACT = 60
_TITLE_BASE = 40
_TITLE_CONTAIN = 25
_ARTIST_HIT = 30
_ARTIST_MISS = -20
_ALBUM_HIT = 10

# 低于此分宁可不回写：留"未分类"只是维持现状，写错专辑却会导到错误目录
MIN_SCORE = 60

# 专辑类型启发式的分档上限（含）
_SINGLE_MAX_TRACKS = 1
_EP_MAX_TRACKS = 6


def _key(value: object) -> str:
    """归一化文本用于宽松比较：去空白与连接符、统一大小写。"""
    return _JOINER.sub("", str(value or "")).casefold()


def _base(value: object) -> str:
    """在 :func:`_key` 基础上去掉括号补充，得到"基名"。"""
    return _key(_BRACKET.sub("", str(value or "")))


def _artist_keys(value: object) -> set[str]:
    """把歌手字段拆成归一化后的集合，兼容"许嵩、何曼婷"这类多歌手串。"""
    return {_key(name) for name in split_artists(value) if _key(name)}


def song_media_id(song: dict) -> str:
    """把一首 LX 歌曲编码成本来源下的 ``media_id``：``<平台>:<原生ID>``。

    跨平台的 ``songmid`` 会撞号，所以平台前缀必须带上，否则 ``wy`` 和 ``tx``
    的同号歌曲会共用一个身份。
    """
    source = str(song.get("source") or "").strip()
    songmid = str(song.get("songmid") or song.get("id") or "").strip()
    return f"{source}:{songmid}" if source and songmid else ""


def parse_media_id(media_id: object) -> tuple[str, str]:
    """拆开 ``<平台>:<原生ID>``；任一段为空时返回 ``("", "")``。"""
    raw = str(media_id or "").strip()
    source, _, native = raw.partition(":")
    source, native = source.strip(), native.strip()
    return (source, native) if source and native else ("", "")


def _album_media_id(song: dict) -> str:
    """取歌曲所属专辑的 media_id，平台没给 albumId 时返回空串。"""
    album_id = song.get("albumId") or (song.get("meta") or {}).get("albumId")
    source = str(song.get("source") or "").strip()
    return f"{source}:{album_id}" if album_id and source else ""


def _duration_seconds(value: object) -> Optional[int]:
    """把 ``mm:ss`` / ``hh:mm:ss`` 转成秒数，格式不符时返回 None。"""
    parts = [part for part in str(value or "").split(":") if part != ""]
    if len(parts) < 2:
        return None
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
    except ValueError:
        return None
    return seconds


def score_candidate(payload: dict, song: dict) -> int:
    """给候选歌曲打分，标题完全不沾边时直接返 0 淘汰。

    标题权重远高于歌手与专辑：本地文件名解析出的歌手经常缺失或是错的，
    而标题是唯一可靠的锚点。
    """
    query_title = _key(payload.get("title"))
    song_title = _key(song.get("name"))
    if not query_title or not song_title:
        return 0

    if query_title == song_title:
        score = _TITLE_EXACT
    elif _base(payload.get("title")) == _base(song.get("name")):
        # 基名相同但括号补充不同：可能是 live / 重制版，先记分再由阈值裁决
        score = _TITLE_BASE
    elif query_title in song_title or song_title in query_title:
        score = _TITLE_CONTAIN
    else:
        return 0

    local_artists = _artist_keys(payload.get("artists"))
    remote_artists = _artist_keys(song.get("singer"))
    if local_artists and remote_artists:
        score += _ARTIST_HIT if local_artists & remote_artists else _ARTIST_MISS

    local_album = _key(payload.get("album"))
    if local_album and local_album == _key(song.get("albumName")):
        score += _ALBUM_HIT

    return score


def pick_best(payload: dict, songs: Iterable[dict]) -> Optional[tuple[dict, int]]:
    """返回得分最高的候选与它的分数，全部低于阈值时返回 None。"""
    best: Optional[tuple[dict, int]] = None
    for song in songs:
        if not isinstance(song, dict):
            continue
        score = score_candidate(payload, song)
        if best is None or score > best[1]:
            best = (song, score)
    if best is None or best[1] < MIN_SCORE:
        return None
    return best


def guess_album_type(total_tracks: Optional[int]) -> str:
    """按曲目数推断专辑类型。

    LX 各平台都不提供发行类型（网易/QQ 的接口里根本没有这个字段），只能按体量推断：
    1 首按单曲、2-6 首按 EP、其余按专辑。取不到曲目数时保守按专辑处理——
    这是绝大多数内容的形态，也是本体的原生兜底取值。
    """
    if not total_tracks or total_tracks <= 0:
        return "Album"
    if total_tracks <= _SINGLE_MAX_TRACKS:
        return "Single"
    if total_tracks <= _EP_MAX_TRACKS:
        return "EP"
    return "Album"


def locate_track(album_payload: dict, song: dict) -> tuple[Optional[int], Optional[int]]:
    """在专辑曲目表里定位当前歌曲，返回 ``(曲序, 总曲目数)``。

    各平台普遍丢弃接口里的``曲序``字段（网易的 ``filterList`` 就丢掉了 ``item.no``），
    所以只能靠 ``songmid`` 反查下标再 +1。查不到时返回 ``(None, 总曲目数)``。
    """
    tracks = [item for item in (album_payload.get("list") or []) if isinstance(item, dict)]
    total = len(tracks) or None

    target = str(song.get("songmid") or song.get("id") or "")
    if not target:
        return None, total

    for index, item in enumerate(tracks, start=1):
        if str(item.get("songmid") or item.get("id") or "") == target:
            return index, total
    return None, total


def _year_from_date(value: object) -> Optional[int]:
    """从 ``2024-06-01`` 这类日期串里取出年份。"""
    match = re.match(r"\s*(\d{4})", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def build_mediainfo(
    payload: dict,
    song: dict,
    album_payload: Optional[dict] = None,
) -> dict:
    """组装回写用的 MusicInfo 字典。

    凡本地已有证据的字段一律回显本地值（详见模块头部第 1 条），
    这里只补充本地拿不到的事实：远端身份、专辑类型、曲序、发行日期、封面。
    """
    artists = [str(name) for name in (payload.get("artists") or []) if name]
    media_id = song_media_id(song)

    info: dict[str, Any] = {
        "music_type": "recording",
        "media_source": MEDIA_SOURCE,
        "media_id": media_id,
        # 标题与艺术家回显本地证据，避免与本地标签冲突导致整条结果被丢弃
        "title": payload.get("title") or song.get("name"),
        "artists": artists or split_artists(song.get("singer")),
    }
    if info["artists"]:
        info["artist"] = "、".join(info["artists"])
    album_id = _album_media_id(song)
    if album_id:
        info["album_id"] = album_id

    # 专辑名与年份：本地有值就回显本地值，没有才用 LX 的
    local_album = payload.get("album")
    remote_album = (album_payload or {}).get("name") or song.get("albumName")
    if local_album:
        info["album"] = local_album
    elif remote_album:
        info["album"] = remote_album

    publish_date = (album_payload or {}).get("publishTime")
    local_year = payload.get("year")
    if local_year:
        info["year"] = local_year
    else:
        year = _year_from_date(publish_date)
        if year:
            info["year"] = year
    if publish_date:
        info["release_date"] = str(publish_date)

    track_number, total_tracks = locate_track(album_payload or {}, song)
    if track_number:
        info["track_number"] = track_number
        info["disc_number"] = 1
    if total_tracks:
        info["total_tracks"] = total_tracks
        info["total_discs"] = 1
    # LX 不提供发行类型，按曲目数推断后交给分类策略
    info["album_type"] = guess_album_type(total_tracks)

    if song.get("img"):
        info["cover_url"] = song["img"]
    seconds = _duration_seconds(song.get("interval"))
    if seconds:
        info["duration"] = seconds
    return info


def build_candidate_info(song: dict) -> Optional[dict]:
    """把一首 LX 歌曲转成搜索候选用的 MusicInfo 字典。

    这里刻意**不**回显任何本地证据：候选列表是摆给用户挑的，只需要远端身份和展示字段。
    与 :func:`build_mediainfo` 的分工就是"给机器匹配"和"给人挑选"。
    """
    media_id = song_media_id(song)
    if not media_id:
        return None
    artists = [str(name) for name in split_artists(song.get("singer")) if name]
    info: dict[str, Any] = {
        "music_type": "recording",
        "media_source": MEDIA_SOURCE,
        "media_id": media_id,
        "title": song.get("name"),
        "artists": artists,
    }
    if artists:
        info["artist"] = "、".join(artists)
    if song.get("albumName"):
        info["album"] = song["albumName"]
    album_id = _album_media_id(song)
    if album_id:
        info["album_id"] = album_id
    if song.get("img"):
        info["cover_url"] = song["img"]
    seconds = _duration_seconds(song.get("interval"))
    if seconds:
        info["duration"] = seconds
    return info


def build_album_detail(album_payload: dict, media_id: str) -> dict:
    """把 LX 的专辑曲目表转成 MusicAlbumInfo 字典。

    ``media_id`` 必须**原样**回填：宿主用字符串严格比对，改一个字面整条就被丢弃。
    """
    tracks: list[dict] = []
    for index, song in enumerate(album_payload.get("list") or [], start=1):
        if not isinstance(song, dict):
            continue
        track = build_candidate_info(song)
        if not track:
            continue
        # 曲序取自数组下标：各平台普遍在接口层就丢掉了原始 track no
        track["album"] = album_payload.get("name")
        track["album_id"] = media_id
        track["track_number"] = index
        track["disc_number"] = 1
        tracks.append(track)

    info: dict[str, Any] = {
        "music_type": "album",
        "media_source": MEDIA_SOURCE,
        "media_id": media_id,
        "title": album_payload.get("name"),
        "tracks": tracks,
    }
    if tracks:
        artists = list(tracks[0].get("artists") or [])
        if artists:
            info["artists"] = artists
            info["artist"] = "、".join(artists)
        info["album"] = info["title"]
        info["total_tracks"] = len(tracks)
        info["total_discs"] = 1
        # LX 不给发行类型，与单曲一致按体量推断
        info["album_type"] = guess_album_type(len(tracks))
    publish_date = album_payload.get("publishTime")
    if publish_date:
        info["release_date"] = str(publish_date)
        year = _year_from_date(publish_date)
        if year:
            info["year"] = year
    return info


class LxMusicRecognizer:
    """按事件载荷在 LX 服务端检索，并组装成 MP 的音乐识别回写负载。

    实例持有缓存，应按插件实例复用；``client_factory`` 每次调用重新取客户端，
    这样 token 过期重建后无需通知这里。
    """

    def __init__(
        self,
        client_factory: Callable[[], LxServerClient],
        source: str = "wy",
        limit: int = 10,
        timeout: float = 8.0,
        cache_ttl: float = 3600.0,
        cache_size: int = 256,
    ) -> None:
        """记录检索参数与缓存上限。"""
        self._client_factory = client_factory
        self._source = (source or "wy").strip().lower() or "wy"
        self._limit = max(1, min(int(limit), 30))
        self._timeout = max(1.0, float(timeout))
        self._cache_ttl = max(0.0, float(cache_ttl))
        self._cache_size = max(8, int(cache_size))
        self._lock = threading.Lock()
        # 键 -> (写入时刻, 结果)；结果可能是 None（负缓存），避免反复查同一首查不到的歌
        self._songs: "OrderedDict[str, tuple[float, Optional[dict]]]" = OrderedDict()
        self._albums: "OrderedDict[str, Optional[dict]]" = OrderedDict()
        # media_id -> 歌曲原始数据。服务端没有"按歌曲 ID 取详情"的接口，
        # 所以搜索结果必须先登记，宿主带着 media_id 回来时才答得上（详见 recognize_by_id）。
        self._identity: "OrderedDict[str, dict]" = OrderedDict()

    @property
    def source(self) -> str:
        """当前使用的检索平台。"""
        return self._source

    def clear(self) -> None:
        """清空缓存，配置变更或插件停止时调用。"""
        with self._lock:
            self._songs.clear()
            self._albums.clear()
            self._identity.clear()

    # ------------------------------------------------------------------ #
    #                              主流程                                  #
    # ------------------------------------------------------------------ #
    def recognize(self, payload: dict) -> Optional[dict]:
        """把事件载荷解析成 MusicInfo 字典，无法可靠匹配时返回 None。"""
        title = str(payload.get("title") or "").strip()
        if not title:
            return None
        # 只处理单曲级请求：专辑/艺术家级识别没有对应曲目，硬写会污染分类
        if str(payload.get("music_type") or "recording") != "recording":
            return None

        cache_key = "|".join(
            (
                self._source,
                _key(title),
                ",".join(sorted(_artist_keys(payload.get("artists")))),
                _key(payload.get("album")),
            )
        )
        hit, cached = self._cache_get(self._songs, cache_key)
        if hit:
            return cached

        result: Optional[dict] = None
        try:
            result = self._lookup(payload, title)
        except LxServerError as err:
            logger.debug(f"LX 识别查询失败，本次跳过：{err}")
        except Exception as err:  # noqa: BLE001  识别失败绝不能影响整理主流程
            logger.warn(f"LX 识别查询异常，本次跳过：{err}")

        self._cache_put(self._songs, cache_key, result)
        return result

    def _lookup(self, payload: dict, title: str) -> Optional[dict]:
        """检索候选并补齐专辑级信息。"""
        song = self._search_best(payload, title)
        if not song:
            logger.debug(f"LX 未匹配到可靠候选：{title}")
            return None

        album_payload = self._album_info(song)
        info = build_mediainfo(payload, song, album_payload)
        logger.info(
            f"LX 识别命中：{info.get('artist')} - {info.get('title')}"
            f"（{info.get('media_source')}:{info.get('media_id')}"
            f"，{info.get('album_type')}"
            f"，曲序 {info.get('track_number') or '-'}/{info.get('total_tracks') or '-'}）"
        )
        return info

    def _search_best(self, payload: dict, title: str) -> Optional[dict]:
        """选出得分最高的候选，并顺带登记身份。"""
        songs = self._search_songs(payload, title, self._limit)
        best = pick_best(payload, songs)
        if not best:
            return None
        song, score = best
        self.remember(song)
        logger.debug(f"LX 候选最高分 {score}：{song.get('name')} - {song.get('singer')}")
        return song

    def _search_songs(self, payload: dict, title: str, limit: int) -> list[dict]:
        """构造查询词并调用服务端搜索。

        服务端的 song 搜索**只吃 name 参数、完全忽略 singer**，所以歌手要拼进查询词
        一起送，否则「幻听」会搜出一堆同名翻唱。
        """
        artists = [name for name in split_artists(payload.get("artists")) if name]
        keyword = f"{artists[0]} {title}".strip() if artists else title
        songs = self._client_factory().search(
            keyword,
            source=self._source,
            limit=max(1, int(limit)),
            timeout=self._timeout,
        )
        return [song for song in songs if isinstance(song, dict)]

    def _album_info(self, song: dict) -> Optional[dict]:
        """取歌曲所属专辑的曲目表。"""
        source, album_id = parse_media_id(_album_media_id(song))
        return self._album_payload(source, album_id)

    def _album_payload(self, source: str, album_id: str) -> Optional[dict]:
        """按平台 + 专辑原始 ID 取曲目表并缓存；平台不支持或上游失败时返回 None。

        带缓存很关键：同一批整理里几十首歌往往同属一张专辑，去重后只查一次。
        """
        if not source or not album_id or source not in ALBUM_SONG_SOURCES:
            return None

        cache_key = f"{source}:{album_id}"
        with self._lock:
            if cache_key in self._albums:
                self._albums.move_to_end(cache_key)
                return self._albums[cache_key]

        try:
            payload = self._client_factory().album_songs(
                album_id, source=source, timeout=self._timeout
            )
        except LxServerError as err:
            logger.debug(f"LX 专辑曲目查询失败，降级为仅身份回写：{err}")
            payload = None
        if payload is not None and not isinstance(payload, dict):
            payload = None

        with self._lock:
            self._albums[cache_key] = payload
            while len(self._albums) > self._cache_size:
                self._albums.popitem(last=False)
        return payload

    # ------------------------------------------------------------------ #
    #                      下载落盘时的标签补全                             #
    # ------------------------------------------------------------------ #
    def album_type_for(self, song: dict) -> Optional[str]:
        """给一首 LX 歌曲推断发行类型，取不到可靠曲目数时返回 **None**。

        供下载落盘时给本地标签补 ``releasetype`` 用（见 ``downloader.apply_album_type``）。
        本地标签一旦带上发行类型，整理就会走 ``_local_music_context`` 的快速通道并按它
        分类，不必再等在线识别。

        取不到曲目数时刻意返回 None 而不是 ``guess_album_type(None)`` 的 "Album"：
        标签留空只是维持"未分类"的现状，写错却会把单曲 / EP 导进专辑目录。
        所以这里只做"确凿才写"。
        """
        if not isinstance(song, dict):
            return None
        try:
            total = self._track_count(song)
        except LxServerError as err:
            logger.debug(f"LX 专辑曲目查询失败，本次不写发行类型：{err}")
            return None
        except Exception as err:  # noqa: BLE001  补标签是锦上添花，绝不能影响下载
            logger.warn(f"LX 专辑曲目查询异常，本次不写发行类型：{err}")
            return None
        if not total:
            return None
        return guess_album_type(total)

    def _track_count(self, song: dict) -> int:
        """取歌曲所属专辑的曲目数，取不到返回 0。"""
        payload = self._album_info(song)
        if not isinstance(payload, dict):
            payload = self._album_info_from_other_source(song)
        if not isinstance(payload, dict):
            return 0
        return len([item for item in (payload.get("list") or []) if isinstance(item, dict)])

    def _album_info_from_other_source(self, song: dict) -> Optional[dict]:
        """本平台没有专辑曲目接口时，改由识别平台重定位同一首歌再取专辑。

        下载平台（默认 kw）与识别平台（默认 wy）通常是两个平台，而专辑曲目接口
        只有 tx / wy 实现；不跨源的话默认配置下永远拿不到曲目数，这个功能就等于没开。

        只有高分匹配（:data:`MIN_SCORE`）才认，避免把同名翻唱 / 不同版本的曲目数
        当成事实写进标签。识别平台与歌曲同源时不做无谓重试（上面已经试过一次）。
        """
        title = str(song.get("name") or "").strip()
        if not title:
            return None
        if self._source == str(song.get("source") or "").strip().lower():
            return None
        payload = {"title": title, "artists": split_artists(song.get("singer"))}
        best = pick_best(payload, self._search_songs(payload, title, self._limit))
        if not best:
            logger.debug(f"LX 未找到可用于推断发行类型的高分候选：{title}")
            return None
        matched = best[0]
        self.remember(matched)
        return self._album_info(matched)

    # ------------------------------------------------------------------ #
    #                    模块通道（注册为宿主数据源）                        #
    # ------------------------------------------------------------------ #
    # 下面四个方法服务于宿主的模块调度（``get_module()``），与上面的链式事件通道
    # 共用同一份匹配逻辑，区别只在"谁发起"：链式事件是宿主识别失败后反问插件，
    # 模块通道是宿主把本来源当成一等数据源直接调用。
    def search_candidates(self, payload: dict, limit: int) -> list[dict]:
        """返回搜索候选，并把每首候选登记进身份索引。

        与 :meth:`recognize` 的区别：这里服务的是"人来挑"，所以只筛掉缺身份的条目，
        **不按分数淘汰**——把沾边的候选一并摆出来，比让用户搜不到更合适。
        打分淘汰只用于 :meth:`recognize` 那条无人值守的路径。
        """
        title = str(payload.get("title") or "").strip()
        if not title:
            return []
        infos: list[dict] = []
        for song in self._search_songs(payload, title, limit):
            info = build_candidate_info(song)
            if not info:
                continue
            self.remember(song)
            infos.append(info)
        return infos

    def recognize_by_id(self, media_id: str, payload: Optional[dict] = None) -> Optional[dict]:
        """按 ``media_id`` 回放一次识别结果。

        宿主在"搜索候选被选中""重新识别""手动刮削"里都会带着 ``media_source`` +
        ``media_id`` 回来，这条路径必须答得上，否则用户点完候选拿不到详情。

        ⚠️ **身份索引只在进程内**，由 :meth:`search_candidates` 与 :meth:`_search_best`
        写入。服务端没有"按歌曲 ID 取详情"的接口（只有 albumSongs），所以索引缺失时
        **无法反查**，只能返回 None —— 表现为重启后重新刮削旧条目会识别失败。
        专辑级身份不受此限：专辑详情直接用 albumId 查（见 :meth:`album_detail`）。
        """
        song = self.song_by_media_id(media_id)
        if song is None:
            logger.debug(f"LX 身份索引未命中（服务端无按 ID 查歌接口）：{media_id}")
            return None

        info = build_mediainfo(payload or {}, song, self._album_info(song))
        expected = str(media_id).strip()
        if info.get("media_id") != expected:
            logger.warn(
                f"LX 身份索引与请求不一致，已忽略：索引 {info.get('media_id')} ≠ 请求 {expected}"
            )
            return None
        logger.info(
            f"LX 识别命中（按身份）：{info.get('artist')} - {info.get('title')}"
            f"（{info.get('media_source')}:{info.get('media_id')}）"
        )
        return info

    def album_detail(self, media_id: str) -> Optional[dict]:
        """按专辑 ``media_id`` 取专辑详情；平台未实现该接口时返回 None。"""
        source, album_id = parse_media_id(media_id)
        if not source or not album_id:
            return None
        if source not in ALBUM_SONG_SOURCES:
            logger.debug(f"LX 平台 {source} 未实现专辑曲目接口，跳过：{media_id}")
            return None
        payload = self._album_payload(source, album_id)
        if not isinstance(payload, dict):
            return None
        return build_album_detail(payload, str(media_id).strip())

    def remember(self, song: dict) -> None:
        """把歌曲登记进身份索引，供后续按 media_id 反查。"""
        media_id = song_media_id(song)
        if not media_id:
            return
        with self._lock:
            self._identity[media_id] = song
            self._identity.move_to_end(media_id)
            while len(self._identity) > self._cache_size:
                self._identity.popitem(last=False)

    def song_by_media_id(self, media_id: object) -> Optional[dict]:
        """按 media_id 取回已登记的歌曲。"""
        key = str(media_id or "").strip()
        if not key:
            return None
        with self._lock:
            song = self._identity.get(key)
            if song is not None:
                self._identity.move_to_end(key)
            return song

    # ------------------------------------------------------------------ #
    #                               缓存                                   #
    # ------------------------------------------------------------------ #
    def _cache_get(
        self, store: "OrderedDict[str, tuple[float, Optional[dict]]]", key: str
    ) -> tuple[bool, Optional[dict]]:
        """返回 ``(是否命中, 结果)``。

        必须把"命中负缓存"和"未命中"区分开：前者代表**已经确认查不到**，直接返回 None
        即可；后者要继续走查询。合在一起会让负缓存彻底失效。
        """
        with self._lock:
            entry = store.get(key)
            if entry is None:
                return False, None
            stamp, value = entry
            if self._cache_ttl and time.time() - stamp > self._cache_ttl:
                store.pop(key, None)
                return False, None
            store.move_to_end(key)
            return True, value

    def _cache_put(
        self, store: "OrderedDict[str, tuple[float, Optional[dict]]]", key: str, value: Optional[dict]
    ) -> None:
        """写入缓存并淘汰最旧条目。"""
        with self._lock:
            store[key] = (time.time(), value)
            store.move_to_end(key)
            while len(store) > self._cache_size:
                store.popitem(last=False)
