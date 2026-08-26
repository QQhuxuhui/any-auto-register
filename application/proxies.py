from __future__ import annotations

import threading

from core.proxy_pool import proxy_pool
from domain.proxies import ProxyBulkCreateCommand, ProxyCheckSummary, ProxyCreateCommand, ProxyRecord
from infrastructure.proxies_repository import ProxiesRepository

_PROBE_LOCK = threading.Lock()
_PROBE_STATE: dict = {"running": False, "total": 0, "done": 0}


class ProxiesService:
    def __init__(self, repository: ProxiesRepository | None = None):
        self.repository = repository or ProxiesRepository()

    def list_proxies(self) -> list[dict]:
        return [self._serialize(item) for item in self.repository.list()]

    def create_proxy(self, command: ProxyCreateCommand) -> dict | None:
        item = self.repository.create(command)
        return self._serialize(item) if item else None

    def bulk_create_proxies(self, command: ProxyBulkCreateCommand) -> dict:
        added = self.repository.bulk_create(command.proxies, command.region)
        return {"added": added}

    def delete_proxy(self, proxy_id: int) -> dict:
        return {"ok": self.repository.delete(proxy_id)}

    def toggle_proxy(self, proxy_id: int) -> dict | None:
        value = self.repository.toggle(proxy_id)
        if value is None:
            return None
        return {"is_active": value}

    def trigger_check(self) -> dict:
        threading.Thread(target=proxy_pool.check_all, daemon=True, name="proxy-check").start()
        return {"message": "检测任务已启动"}

    @staticmethod
    def _serialize(item: ProxyRecord) -> dict:
        return {
            "id": item.id,
            "url": item.url,
            "region": item.region,
            "success_count": item.success_count,
            "fail_count": item.fail_count,
            "is_active": item.is_active,
            "last_checked": item.last_checked,
            "ip_type": item.ip_type,
            "country": item.country,
            "isp": item.isp,
            "egress_ip": item.egress_ip,
            "latency_ms": item.latency_ms,
            "probe_status": item.probe_status,
            "probed_at": item.probed_at,
        }

    # ── IP 类型探测 ────────────────────────────────────────
    def start_probe(self) -> dict:
        with _PROBE_LOCK:
            if _PROBE_STATE.get("running"):
                return {"message": "探测已在进行中", **_PROBE_STATE}
            items = self.repository.list_id_url()
            _PROBE_STATE.update(running=True, total=len(items), done=0)
        threading.Thread(target=self._run_probe, args=(items,), daemon=True, name="proxy-probe").start()
        return {"message": f"已开始探测 {len(items)} 条代理", "total": len(items)}

    def _run_probe(self, items: list) -> None:
        from core.proxy_probe import probe_all
        def _on(pid, res):
            try:
                self.repository.update_probe(pid, res)
            finally:
                with _PROBE_LOCK:
                    _PROBE_STATE["done"] += 1
        try:
            probe_all(items, workers=16, on_result=_on)
        finally:
            with _PROBE_LOCK:
                _PROBE_STATE["running"] = False

    @staticmethod
    def probe_status() -> dict:
        with _PROBE_LOCK:
            return dict(_PROBE_STATE)

    def delete_by_type(self, types: list[str], only_dead: bool = False) -> dict:
        return {"deleted": self.repository.delete_by(types=types, only_dead=only_dead)}

    def bulk_delete(self, ids: list[int]) -> dict:
        return {"deleted": self.repository.bulk_delete(ids)}
