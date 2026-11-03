# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The ``/nodeconnect/v1`` OpenAPI document (NCON-NFR-030).

Built from ``wire.OPERATIONS`` so it cannot list a route that is not served, or miss one that is; the committed
copy in ``api/nodeconnect-v1.openapi.json`` is checked against this by ``tests/test_ncon_wire.py``.
Regenerate it with ``python -m nodeconnect.openapi > api/nodeconnect-v1.openapi.json``.

Every route sits behind Connect Link's authenticated, encrypted transport: a request without a valid token, with a
revoked one or with a forged one gets ``401`` and no data. The caller is the authenticated paired node; no body
names who it is.
"""

from __future__ import annotations

import json
from typing import Any

from nodeconnect.wire import API, OPERATIONS

VERSION = "1.0.0"

_OBJECT = {"type": "object"}
_CHANGE = {
    "type": "object", "required": ["table", "op", "key"],
    "properties": {
        "table": {"type": "string"}, "op": {"type": "string", "enum": ["upsert", "delete"]},
        "key": {"type": "array", "description": "The natural key; a parent is named by its own natural key."},
        "wire": {"type": ["object", "null"], "description": "The row in wire form (no secrets, no node-local fields)."},
        "rev": {"type": "integer"}, "tomb": {"type": "integer", "description": "A deletion's tombstone id."},
    },
}
_PUSHED = {
    "type": "object", "required": ["table", "op", "key", "base_rev", "modified"],
    "properties": {
        "table": {"type": "string"}, "op": {"type": "string", "enum": ["upsert", "delete"]}, "key": {"type": "array"},
        "wire": {"type": ["object", "null"]}, "base_rev": {"type": "integer", "description":
                 "The Master revision this edit was made on; a stale one is the conflict signal."},
        "modified": {"type": "number", "description": "Seconds since the epoch on the node's clock."},
    },
}
_RESULT = {
    "type": "object", "required": ["table", "key", "status", "winner", "rev"],
    "properties": {
        "table": {"type": "string"}, "key": {"type": "array"}, "status": {"type": "string"},
        "winner": {"type": "string", "enum": ["", "node", "master"]}, "rev": {"type": "integer"},
        "op": {"type": "string"}, "wire": {"type": ["object", "null"]},
    },
}

_DOCS: dict[str, dict[str, Any]] = {
    "handshake": {
        "summary": "Exchange versions, column fingerprints and clock readings.",
        "request": {"type": "object", "properties": {"info": {**_OBJECT, "description": "The node's version information."}}},
        "response": {"type": "object", "required": ["info", "fingerprints", "received", "sent"], "properties": {
            "info": _OBJECT, "fingerprints": {"type": "object", "additionalProperties": {"type": "string"}},
            "received": {"type": "number"}, "sent": {"type": "number"}}},
    },
    "pull": {
        "summary": "Changes above the node's per-table revisions, a table at a time in pages.",
        "request": {"type": "object", "properties": {
            "since": {"type": "object", "additionalProperties": {"type": "integer"},
                      "description": "Revision per table; '<table>#t' is the tombstone cursor."},
            "tables": {"type": ["array", "null"], "items": {"type": "string"}}}},
        "response": {"type": "object", "required": ["pages"], "properties": {"pages": {"type": "array", "items": {
            "type": "object", "required": ["table", "rev", "tomb", "changes"], "properties": {
                "table": {"type": "string"}, "rev": {"type": "integer"}, "tomb": {"type": "integer"},
                "changes": {"type": "array", "items": _CHANGE}}}}}},
    },
    "push": {
        "summary": "Send the node's own edits; the Master decides each and answers with the result.",
        "request": {"type": "object", "required": ["changes"], "properties": {
            "changes": {"type": "array", "items": _PUSHED},
            "offset": {"type": "object", "properties": {"seconds": {"type": "number"}, "error": {"type": "number"}}}}},
        "response": {"type": "object", "required": ["results"], "properties": {
            "results": {"type": "array", "items": _RESULT}}},
    },
    "ack": {
        "summary": "Tell the Master which revisions the node has applied, so it can release tombstones.",
        "request": {"type": "object", "properties": {"revs": {"type": "object", "additionalProperties": {"type": "integer"}}}},
        "response": {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
    },
}


def document() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for suffix, _method, permission in OPERATIONS:
        spec = _DOCS[suffix]
        paths[f"{API}/{suffix}"] = {"post": {
            "operationId": f"nodeconnect_{suffix}", "summary": spec["summary"],
            "description": f"Needs the `{permission}` permission.", "x-permission": permission,
            "security": [{"connectLinkToken": []}],
            "requestBody": {"required": False, "content": {"application/json": {"schema": spec["request"]}}},
            "responses": {
                "200": {"description": "OK", "content": {"application/json": {"schema": spec["response"]}}},
                "400": {"description": "A malformed request or a table the Master does not replicate."},
                "401": {"description": "No valid token (missing, revoked or forged). No data is returned."},
                "403": {"description": "The node's pairing does not carry the permission."},
                "409": {"description": "This node is a spoke of another Master and does not serve nodes."},
            }}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "NodeConnect", "version": VERSION,
                 "description": "Configuration sync between a spoke and its Master, over Connect Link's authenticated, "
                                "encrypted transport."},
        "paths": paths,
        "components": {"securitySchemes": {"connectLinkToken": {
            "type": "http", "scheme": "bearer", "description": "The token issued when the node paired."}}},
    }


def dumps() -> str:
    return json.dumps(document(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(dumps(), end="")
