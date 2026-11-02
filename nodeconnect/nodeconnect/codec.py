# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""What crosses the wire, and how a received row is merged (NCON-100, NCON-110, NCON-210).

``ItemFilter`` decides per item whether it may leave the node: node-local items stay home and secrets
are never sent unless the user opted that item in. ``encode_pages`` batches rows into pages of at
most 200 and never builds a whole table in memory. ``merge_fields`` writes a received row onto the
local one field by field, so a node-local value on the receiving node is never overwritten, even when
the sender included it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

PAGE_SIZE = 200


class ItemFilter:
    """*optins* is a set of ``"<table>:<field>"`` or ``"<table>:*"`` strings the user ticked on the
    Options page (to replicate a node-local field or a secret)."""

    def __init__(self, optins: Iterable[str] = ()) -> None:
        self._optins = frozenset(optins)

    def opted_in(self, table: str, field: str) -> bool:
        return f"{table}:{field}" in self._optins or f"{table}:*" in self._optins

    def allow_row(self, spec, row: dict[str, Any]) -> bool:
        """False for a row of a node-local kind (for example the link's own settings)."""
        if not spec.exclude_row(row):
            return True
        return self.opted_in(spec.table, "*")

    def scrub(self, spec, wire: dict[str, Any]) -> dict[str, Any]:
        """The wire form with node-local and secret fields removed unless opted in. Defence in depth:
        a spec's own ``to_wire`` should already omit them."""
        fields = wire.get("fields")
        if not isinstance(fields, dict):
            return wire
        banned = (spec.node_local_fields | spec.secret_fields)
        kept = {k: v for k, v in fields.items() if k not in banned or self.opted_in(spec.table, k)}
        return {**wire, "fields": kept}


def encode_pages(spec, rows: Iterable[dict[str, Any]], item_filter: ItemFilter,
                 page_size: int = PAGE_SIZE) -> Iterator[list[dict[str, Any]]]:
    """Wire pages for *rows* of *spec*'s table; rows may be a generator."""
    page: list[dict[str, Any]] = []
    for row in rows:
        if not item_filter.allow_row(spec, row):
            continue
        page.append(item_filter.scrub(spec, spec.to_wire(row)))
        if len(page) >= page_size:
            yield page
            page = []
    if page:
        yield page


def merge_fields(spec, local: dict[str, Any] | None, incoming: dict[str, Any],
                 item_filter: ItemFilter | None = None) -> dict[str, Any]:
    """The row to write for *incoming* (a row as returned by ``spec.from_wire``) over *local*.

    Node-local fields keep the local value (and take the incoming one only on insert, when there is no
    local value, or when opted in); everything else takes the incoming value.
    """
    item_filter = item_filter or ItemFilter()
    if local is None:
        return {k: v for k, v in incoming.items() if k not in spec.secret_fields or item_filter.opted_in(spec.table, k)}
    merged = dict(local)
    for k, v in incoming.items():
        if k in spec.node_local_fields and not item_filter.opted_in(spec.table, k):
            continue
        if k in spec.secret_fields and not item_filter.opted_in(spec.table, k):
            continue
        merged[k] = v
    return merged
