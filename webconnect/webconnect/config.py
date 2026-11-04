# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""WebConnect options as plain data, every value clamped on the way in (SDD §3.13, WCON-060, WCON-910)."""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, fields
from typing import Any


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


@dataclass(frozen=True)
class WebConnectConfig:
    bind_address: str = ""              # "" = the Master's LAN interface; never 0.0.0.0 unless wide_bind is set
    wide_bind: bool = False             # explicit opt-in to bind every interface (WCON-060)
    port: int = 8443
    start_with_galileo: bool = True
    session_idle_s: int = 1800          # WCON-1040
    session_absolute_s: int = 28800
    lease_idle_s: int = 900             # WCON-140
    audit_retention_days: int = 90      # WCON-190
    preview_quality: int = 80
    preview_max_px: int = 1600
    preview_cache_mb: int = 32
    pair_expiry_s: int = 300            # WCON-100: pending requests lapse after a few minutes

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> WebConnectConfig:
        raw = raw or {}
        d = cls()
        bind = str(raw.get("bind_address", d.bind_address) or "").strip()
        if bind:
            try:
                ipaddress.ip_address(bind)
            except ValueError:
                bind = d.bind_address               # a hostname or garbage is not a bind address
        wide = bool(raw.get("wide_bind", d.wide_bind))
        if bind in ("0.0.0.0", "::") and not wide:
            bind = d.bind_address                   # binding every interface needs the explicit setting
        return cls(
            bind_address=bind, wide_bind=wide,
            port=int(_clamp(raw.get("port", d.port), 1024, 65535, d.port)),
            start_with_galileo=bool(raw.get("start_with_galileo", d.start_with_galileo)),
            session_idle_s=int(_clamp(raw.get("session_idle_s", d.session_idle_s), 60, 86400, d.session_idle_s)),
            session_absolute_s=int(_clamp(raw.get("session_absolute_s", d.session_absolute_s), 300, 7 * 86400,
                                          d.session_absolute_s)),
            lease_idle_s=int(_clamp(raw.get("lease_idle_s", d.lease_idle_s), 30, 86400, d.lease_idle_s)),
            audit_retention_days=int(_clamp(raw.get("audit_retention_days", d.audit_retention_days), 1, 3650,
                                            d.audit_retention_days)),
            preview_quality=int(_clamp(raw.get("preview_quality", d.preview_quality), 20, 95, d.preview_quality)),
            preview_max_px=int(_clamp(raw.get("preview_max_px", d.preview_max_px), 256, 4096, d.preview_max_px)),
            preview_cache_mb=int(_clamp(raw.get("preview_cache_mb", d.preview_cache_mb), 1, 512, d.preview_cache_mb)),
            pair_expiry_s=int(_clamp(raw.get("pair_expiry_s", d.pair_expiry_s), 60, 1800, d.pair_expiry_s)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]
