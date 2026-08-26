from __future__ import annotations

from sqlmodel import Session, select

from core.db import ProxyModel, engine
from domain.proxies import ProxyCreateCommand, ProxyRecord


def _to_record(model: ProxyModel) -> ProxyRecord:
    return ProxyRecord(
        id=int(model.id or 0),
        url=model.url,
        region=model.region,
        success_count=model.success_count,
        fail_count=model.fail_count,
        is_active=bool(model.is_active),
        last_checked=model.last_checked,
        ip_type=getattr(model, "ip_type", "") or "",
        country=getattr(model, "country", "") or "",
        isp=getattr(model, "isp", "") or "",
        egress_ip=getattr(model, "egress_ip", "") or "",
        latency_ms=int(getattr(model, "latency_ms", 0) or 0),
        probe_status=getattr(model, "probe_status", "") or "",
        probed_at=getattr(model, "probed_at", None),
    )


class ProxiesRepository:
    def list(self) -> list[ProxyRecord]:
        with Session(engine) as session:
            items = session.exec(select(ProxyModel)).all()
        return [_to_record(item) for item in items]

    def create(self, command: ProxyCreateCommand) -> ProxyRecord | None:
        with Session(engine) as session:
            existing = session.exec(select(ProxyModel).where(ProxyModel.url == command.url)).first()
            if existing:
                return None
            model = ProxyModel(url=command.url, region=command.region)
            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_record(model)

    def bulk_create(self, urls: list[str], region: str = "") -> int:
        added = 0
        with Session(engine) as session:
            for raw in urls:
                url = raw.strip()
                if not url:
                    continue
                existing = session.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
                if existing:
                    continue
                session.add(ProxyModel(url=url, region=region))
                added += 1
            session.commit()
        return added

    def delete(self, proxy_id: int) -> bool:
        with Session(engine) as session:
            model = session.get(ProxyModel, proxy_id)
            if not model:
                return False
            session.delete(model)
            session.commit()
            return True

    def toggle(self, proxy_id: int) -> bool | None:
        with Session(engine) as session:
            model = session.get(ProxyModel, proxy_id)
            if not model:
                return None
            model.is_active = not model.is_active
            session.add(model)
            session.commit()
            session.refresh(model)
            return bool(model.is_active)

    def list_id_url(self) -> list[tuple[int, str]]:
        with Session(engine) as session:
            items = session.exec(select(ProxyModel)).all()
        return [(int(m.id or 0), m.url) for m in items if m.id]

    def update_probe(self, proxy_id: int, res: dict) -> None:
        from datetime import datetime, timezone
        with Session(engine) as session:
            model = session.get(ProxyModel, proxy_id)
            if not model:
                return
            model.probe_status = str(res.get("status") or "")
            model.latency_ms = int(res.get("latency_ms") or 0)
            model.probed_at = datetime.now(timezone.utc)
            if res.get("status") == "ok":
                model.ip_type = str(res.get("ip_type") or "unknown")
                model.country = str(res.get("country") or "")
                model.isp = str(res.get("isp") or "")
                model.egress_ip = str(res.get("egress_ip") or "")
            session.add(model)
            session.commit()

    def delete_by(self, *, types: list[str] | None = None, only_dead: bool = False) -> int:
        types = [t for t in (types or []) if t]
        deleted = 0
        with Session(engine) as session:
            items = session.exec(select(ProxyModel)).all()
            for m in items:
                match = False
                if types and (m.ip_type or "unknown") in types:
                    match = True
                if only_dead and m.probe_status == "fail":
                    match = True
                if match:
                    session.delete(m)
                    deleted += 1
            session.commit()
        return deleted

    def bulk_delete(self, ids: list[int]) -> int:
        deleted = 0
        with Session(engine) as session:
            for pid in ids:
                m = session.get(ProxyModel, int(pid))
                if m:
                    session.delete(m); deleted += 1
            session.commit()
        return deleted
