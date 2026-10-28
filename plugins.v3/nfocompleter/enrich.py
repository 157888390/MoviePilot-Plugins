# -*- coding: utf-8 -*-
"""TMDB 数据源封装与 NFO 字段补全映射。

通过宿主的 TMDB 模块（``ModuleManager``）按身份定位媒体详情，再把 TMDB 字段映射成
Kodi NFO 的字段值。只负责「取数 + 映射」，不碰文件写回（写回由 ``nfo`` 模块负责）。

定位策略：
- movie / tvshow：优先用 NFO 里已有的 ``uniqueid(tmdb)`` 精确取详情；没有则用
  ``title + year`` 搜索匹配。
- episode：用 ``uniqueid(tmdb)``（Kodi 里通常写的是**剧集**的 tmdb id）或 ``showtitle``
  先定位剧集，再按 ``season + episode`` 从整季中筛出目标集。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.schemas.types import MediaRecognizeType, MediaType
from app.sdk.logging import logger
from app.sdk.plugin import ModuleManager

# 演员 / 导演 / 编剧的补全上限，避免把整张名单塞进 NFO
_ACTOR_LIMIT = 20
_CREW_LIMIT = 10


def get_tmdb_module() -> Optional[Any]:
    """返回宿主当前运行的 TMDB 模块实例；未启用时返回 None。"""
    try:
        modules = list(ModuleManager().get_running_subtype_module(MediaRecognizeType.TMDB))
    except Exception as err:  # noqa: BLE001
        logger.warn(f"获取 TMDB 模块失败：{err}")
        return None
    return modules[0] if modules else None


class TmdbEnricher:
    """把 TMDB 详情映射成 NFO 补全字段。"""

    def __init__(self, module: Optional[Any] = None):
        self.module = module if module is not None else get_tmdb_module()

    @property
    def available(self) -> bool:
        """TMDB 模块是否可用。"""
        return self.module is not None

    # ------------------------------------------------------------------ #
    #                            入口                                     #
    # ------------------------------------------------------------------ #
    def enrich(
        self,
        kind: str,
        identity: Dict[str, Any],
        missing_fields: List[str],
    ) -> Tuple[Dict[str, Any], Optional[int]]:
        """按缺失字段生成补全值。

        :return: (补全字段字典, 建议写入的 tmdb id)；无数据返回 ({}, None)
        """
        if not self.available:
            return {}, None
        try:
            data, tmdb_id = self._resolve(kind, identity)
        except Exception as err:  # noqa: BLE001  单文件补全失败不阻断整体
            logger.warn(f"TMDB 查询失败（{identity.get('title') or identity.get('showtitle')}）：{err}")
            return {}, None
        if not data:
            return {}, None
        try:
            updates = self._build_updates(kind, data, missing_fields)
        except Exception as err:  # noqa: BLE001
            logger.warn(f"TMDB 字段映射失败：{err}")
            return {}, None
        return updates, tmdb_id

    # ------------------------------------------------------------------ #
    #                            定位                                     #
    # ------------------------------------------------------------------ #
    def _resolve(self, kind: str, identity: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[int]]:
        """定位媒体详情，返回 (数据字典, tmdb id)。"""
        if kind == "episode":
            return self._resolve_episode(identity)
        if kind == "movie":
            return self._resolve_movie_or_tv(identity, MediaType.MOVIE)
        if kind == "tvshow":
            return self._resolve_movie_or_tv(identity, MediaType.TV)
        return None, None

    def _resolve_movie_or_tv(
        self, identity: Dict[str, Any], mtype: MediaType
    ) -> Tuple[Optional[Dict], Optional[int]]:
        """按 tmdb id 精确取，或按 title + year 搜索。"""
        tmdb_id = identity.get("tmdb_id")
        if tmdb_id:
            data = self.module.tmdb_info(int(tmdb_id), mtype)
            if data:
                return data, int(data.get("id") or tmdb_id)
            return None, None
        title = identity.get("title")
        if not title:
            return None, None
        data = self.module.match_tmdbinfo(title, mtype, identity.get("year"))
        if not data:
            return None, None
        return data, int(data["id"]) if data.get("id") else None

    def _resolve_episode(self, identity: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[int]]:
        """先定位剧集，再按季集筛选目标集。"""
        season = identity.get("season")
        episode = identity.get("episode")
        if season is None or episode is None:
            return None, None
        try:
            season = int(season)
            episode = int(episode)
        except (TypeError, ValueError):
            return None, None

        show_id = identity.get("tmdb_id")
        if not show_id:
            showtitle = identity.get("showtitle")
            if not showtitle:
                return None, None
            show_data = self.module.match_tmdbinfo(showtitle, MediaType.TV)
            if not show_data or not show_data.get("id"):
                return None, None
            show_id = int(show_data["id"])

        episodes = self.module.tmdb_episodes(int(show_id), season)
        if not episodes:
            return None, None
        for ep in episodes:
            data = ep.model_dump() if hasattr(ep, "model_dump") else ep
            if data.get("episode_number") == episode:
                # 集 NFO 的 uniqueid 写剧集 id（Kodi 用剧集 id + 季/集定位）
                return data, int(show_id)
        return None, None

    # ------------------------------------------------------------------ #
    #                            字段映射                                 #
    # ------------------------------------------------------------------ #
    def _build_updates(self, kind: str, data: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
        if kind == "movie":
            return self._movie_updates(data, missing)
        if kind == "tvshow":
            return self._tvshow_updates(data, missing)
        if kind == "episode":
            return self._episode_updates(data, missing)
        return {}

    @staticmethod
    def _year_from_date(date: Optional[str]) -> Optional[str]:
        if date and len(date) >= 4:
            return date[:4]
        return None

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        text = (text or "").strip()
        return text if len(text) <= limit else text[:limit] + "…"

    @staticmethod
    def _crew_names(data: Dict[str, Any], job: Optional[str] = None, department: Optional[str] = None) -> List[str]:
        names: List[str] = []
        for person in (data.get("credits") or {}).get("crew") or []:
            if job and person.get("job") != job:
                continue
            if department and person.get("department") != department:
                continue
            name = person.get("name")
            if name and name not in names:
                names.append(name)
            if len(names) >= _CREW_LIMIT:
                break
        return names

    @staticmethod
    def _cast(data: Dict[str, Any]) -> List[Dict[str, str]]:
        actors: List[Dict[str, str]] = []
        for person in (data.get("credits") or {}).get("cast") or []:
            name = person.get("name")
            if not name:
                continue
            actors.append({"name": name, "role": person.get("character") or ""})
            if len(actors) >= _ACTOR_LIMIT:
                break
        return actors

    def _movie_updates(self, data: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if "title" in missing and data.get("title"):
            updates["title"] = data["title"]
        if "originaltitle" in missing and data.get("original_title"):
            updates["originaltitle"] = data["original_title"]
        if "year" in missing:
            year = self._year_from_date(data.get("release_date"))
            if year:
                updates["year"] = year
        if "premiered" in missing and data.get("release_date"):
            updates["premiered"] = data["release_date"]
        if "runtime" in missing and data.get("runtime"):
            updates["runtime"] = str(data["runtime"])
        if "plot" in missing and data.get("overview"):
            updates["plot"] = data["overview"]
        if "outline" in missing and data.get("overview"):
            updates["outline"] = self._truncate(data["overview"], 200)
        if "tagline" in missing and data.get("tagline"):
            updates["tagline"] = data["tagline"]
        if "rating" in missing and data.get("vote_average") is not None:
            updates["rating"] = f"{float(data['vote_average']):.1f}"
        if "votes" in missing and data.get("vote_count") is not None:
            updates["votes"] = str(data["vote_count"])
        if "mpaa" in missing and data.get("content_rating"):
            updates["mpaa"] = data["content_rating"]
        if "genre" in missing:
            genres = [g.get("name") for g in data.get("genres") or [] if g.get("name")]
            if genres:
                updates["genre"] = genres
        if "director" in missing:
            directors = self._crew_names(data, job="Director")
            if directors:
                updates["director"] = directors
        if "credits" in missing:
            writers = self._crew_names(data, department="Writing")
            if writers:
                updates["credits"] = writers
        if "studio" in missing:
            studios = [c.get("name") for c in data.get("production_companies") or [] if c.get("name")]
            if studios:
                updates["studio"] = studios[:3]
        if "country" in missing:
            countries = [c.get("iso_3166_1") for c in data.get("production_countries") or [] if c.get("iso_3166_1")]
            if countries:
                updates["country"] = countries
        if "actor" in missing:
            actors = self._cast(data)
            if actors:
                updates["actor"] = actors
        return updates

    def _tvshow_updates(self, data: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if "title" in missing and data.get("name"):
            updates["title"] = data["name"]
        if "originaltitle" in missing and data.get("original_name"):
            updates["originaltitle"] = data["original_name"]
        if "year" in missing:
            year = self._year_from_date(data.get("first_air_date"))
            if year:
                updates["year"] = year
        if "premiered" in missing and data.get("first_air_date"):
            updates["premiered"] = data["first_air_date"]
        if "runtime" in missing:
            runtimes = data.get("episode_run_time") or []
            if runtimes and runtimes[0]:
                updates["runtime"] = str(runtimes[0])
        if "plot" in missing and data.get("overview"):
            updates["plot"] = data["overview"]
        if "outline" in missing and data.get("overview"):
            updates["outline"] = self._truncate(data["overview"], 200)
        if "rating" in missing and data.get("vote_average") is not None:
            updates["rating"] = f"{float(data['vote_average']):.1f}"
        if "votes" in missing and data.get("vote_count") is not None:
            updates["votes"] = str(data["vote_count"])
        if "mpaa" in missing and data.get("content_rating"):
            updates["mpaa"] = data["content_rating"]
        if "genre" in missing:
            genres = [g.get("name") for g in data.get("genres") or [] if g.get("name")]
            if genres:
                updates["genre"] = genres
        if "studio" in missing:
            studios = [n.get("name") for n in data.get("networks") or [] if n.get("name")]
            if studios:
                updates["studio"] = studios
        if "actor" in missing:
            actors = self._cast(data)
            if actors:
                updates["actor"] = actors
        return updates

    @staticmethod
    def _episode_updates(data: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if "title" in missing and data.get("name"):
            updates["title"] = data["name"]
        if "aired" in missing and data.get("air_date"):
            updates["aired"] = data["air_date"]
        if "runtime" in missing and data.get("runtime"):
            updates["runtime"] = str(data["runtime"])
        if "plot" in missing and data.get("overview"):
            updates["plot"] = data["overview"]
        if "rating" in missing and data.get("vote_average") is not None:
            updates["rating"] = f"{float(data['vote_average']):.1f}"
        if "director" in missing:
            directors = [c.get("name") for c in data.get("crew") or [] if c.get("job") == "Director" and c.get("name")]
            if directors:
                updates["director"] = directors
        if "credits" in missing:
            writers = [
                c.get("name") for c in data.get("crew") or []
                if c.get("department") == "Writing" and c.get("name")
            ]
            if writers:
                updates["credits"] = writers
        if "actor" in missing:
            actors = [
                {"name": g.get("name"), "role": g.get("character") or ""}
                for g in data.get("guest_stars") or []
                if g.get("name")
            ][:_ACTOR_LIMIT]
            if actors:
                updates["actor"] = actors
        return updates
