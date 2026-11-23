"""
qBittorrent WebAPI 的最小客户端。

只覆盖反吸血需要的四个动作：登录、列种子、拉某个种子的 peer 列表、封禁 peer。
不依赖宿主 qbittorrent 模块，避免被它的接口版本差异绑住 —— 这里用的都是
WebUI API v2 的稳定端点（qb 4.5 起通行，5.x 同样可用）。

关于 ``peer_id_client``：qb 4.5 起在 peer 对象里带上这个字段，值是从 peer 握手中
解析出的客户端标识（如 ``-XL0012-``），PBH 的 PeerID 过滤读的就是它。更老的 qb
没有这个字段，届时只有客户端名规则可用 —— 这是本插件与 PBH 对齐的下限版本。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import requests


class QbError(Exception):
    """qb 侧返回了不可用的结果（登录失败、接口 404、返回体不是 JSON 等）。"""


class QbClient:
    """按需登录、会话内复用 SID 的轻量客户端。

    同一个实例可跨轮复用：cookie 失效（qb 重启、SID 过期）时会自动重登一次，
    实在登不上就抛 :class:`QbError`，由调用方决定是降级还是报错。
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        timeout: float = 15.0,
    ) -> None:
        """
        :param host: qb 的 WebUI 根地址，例如 ``http://172.17.0.1:18888``
        :param username: WebUI 用户名
        :param password: WebUI 密码
        :param timeout: 单次请求超时（秒）
        """
        self.host = (host or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Referer": self.host, "User-Agent": "MoviePilot-PeerGuard"})
        self._lock = threading.Lock()
        self._logged_in = False

    # ------------------------------------------------------------------
    # 基础请求
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        """拼出完整接口地址。"""
        return f"{self.host}/api/v2{path}"

    def login(self) -> None:
        """登录并保存 SID cookie；失败抛 :class:`QbError`。"""
        if not self.host:
            raise QbError("未配置 qb 地址")
        try:
            resp = self._session.post(
                self._url("/auth/login"),
                data={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise QbError(f"连接 qb 失败：{err}") from err
        # qb 4.x 登录成功返回 200 + "Ok."，失败返回 200 + "Fails."；
        # 5.3 起成功改成 204 空响应体，这里两种都要认。鉴权失败通常是 401/403。
        body = (resp.text or "").strip()
        if resp.status_code == 403:
            raise QbError("qb 因失败次数过多封禁了当前 IP，请先在 WebUI 里解除")
        if resp.status_code == 204:
            self._logged_in = True
            return
        if resp.status_code == 200 and not body.lower().startswith("fails"):
            self._logged_in = True
            return
        raise QbError(f"qb 登录失败（HTTP {resp.status_code}）：{body[:60]}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        raw: bool = False,
    ) -> Any:
        """
        发一次带登录态的请求，403 时重登一次再试。

        :param raw: 为 True 时返回 ``requests.Response``，否则尝试解析 JSON
        """
        with self._lock:
            if not self._logged_in:
                self.login()
            for attempt in (1, 2):
                try:
                    resp = self._session.request(
                        method,
                        self._url(path),
                        params=params,
                        data=data,
                        timeout=self.timeout,
                    )
                except requests.RequestException as err:
                    raise QbError(f"请求 qb 失败：{err}") from err
                if resp.status_code == 403 and attempt == 1:
                    # SID 过期或被踢：重登后重试一次
                    self._logged_in = False
                    self.login()
                    continue
                if resp.status_code not in (200, 204):
                    raise QbError(f"qb 返回 HTTP {resp.status_code}：{(resp.text or '')[:80]}")
                if raw:
                    return resp
                if not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError as err:
                    raise QbError("qb 返回的不是 JSON，可能是地址指向了别的服务") from err
        raise QbError("请求 qb 失败：重试后仍未通过鉴权")

    # ------------------------------------------------------------------
    # 业务动作
    # ------------------------------------------------------------------
    def version(self) -> str:
        """返回 qb 版本号，用于连通性自检。"""
        resp = self._request("GET", "/app/version", raw=True)
        return (resp.text or "").strip()

    def torrents(self) -> List[Dict[str, Any]]:
        """返回全部种子（``/torrents/info`` 的字段集合）。"""
        data = self._request("GET", "/torrents/info")
        return data if isinstance(data, list) else []

    def peers(self, torrent_hash: str) -> Dict[str, Dict[str, Any]]:
        """
        返回某个种子的 peer 明细，key 是 qb 给的 peer 标识。

        ``rid=0`` 表示要全量而不是增量：增量模式要自己维护 rid 游标，
        对本插件的轮询频率来说全量更省心，单种子开销也只有几 KB。
        """
        data = self._request(
            "GET", "/sync/torrentPeers", params={"hash": torrent_hash, "rid": 0}
        )
        if not isinstance(data, dict):
            return {}
        peers = data.get("peers")
        return peers if isinstance(peers, dict) else {}

    def ban_peers(self, peers: List[str]) -> None:
        """
        封禁若干 peer，元素形如 ``1.2.3.4:8080``。

        注意这是 **会话级** 封禁：qb 重启后失效，且没有对应的"解封"接口。
        """
        if not peers:
            return
        self._request("POST", "/transfer/banPeers", data={"peers": "|".join(peers)})


def extract_peer_id(peer: Dict[str, Any]) -> str:
    """
    取 peer 的客户端标识（qb 的 ``peer_id_client`` 字段）。

    注意 ``peers`` 字典的 key 是 ``ip:port`` 而不是 peer_id（qb 源码
    ``synccontroller.cpp`` 里是 ``peers[address.toString()] = peer``），所以
    只能靠这个字段。qb 低于 4.5 时该字段不存在，这里返回空串，PeerID 规则自然跳过，
    只剩客户端名规则生效。
    """
    return str(peer.get("peer_id_client") or "").strip()
