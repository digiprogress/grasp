"""
Graph diff engine.

Pure comparison layer: given the previous graph's {anchor_key: [signature_hash]}
snapshot and the freshly parsed elements, report what was ADDED, CHANGED or
DELETED. No I/O here — the writer supplies the old snapshot
(read_element_state) and the parse supplies the new elements; this module only
compares, which keeps it unit-testable without a database.

Identity is the anchor_key (kind:file:qualified_name, no line number) and the
change signal is the signature_hash — both computed by the same functions the
writer stores, so the diff speaks in exactly the identities the graph holds.

An anchor maps to a LIST of hashes, not a single one: plain INSERTs allow
same-named elements to coexist (two `helper`s in one file), and pretending
otherwise is how identity bugs start. Two states of an anchor are equal iff
their hash multisets are equal — order-insensitive, count-sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .arcadedb_direct_writer import _sig_hash, anchor_key

# The code-element kinds the graph stores, and how to pull each out of a
# parsed-file dict. Interfaces/enums are exported-only — this MUST match the
# writer's INSERT filters, or the diff reports phantom adds/deletes.
CODE_KINDS = ("function", "method", "class", "interface", "enum")

_CONTAINERS = {
    "function": "functions",
    "method": "methods",
    "class": "classes",
    "interface": "interfaces",
    "enum": "enums",
}


def _elements_of(kind: str, f: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = f.get(_CONTAINERS[kind], []) or []
    if kind in ("interface", "enum"):
        return [i for i in items if i.get("is_exported")]
    return items


@dataclass
class DiffReport:
    added: List[Dict[str, Any]] = field(default_factory=list)
    changed: List[Dict[str, Any]] = field(default_factory=list)
    deleted: List[Dict[str, Any]] = field(default_factory=list)
    first_parse: bool = False

    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.deleted)

    def summary(self) -> Dict[str, Any]:
        return {
            "added": len(self.added),
            "changed": len(self.changed),
            "deleted": len(self.deleted),
            "first_parse": self.first_parse,
        }


def parsed_to_elements(parsed_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten parsed files into the element records the diff compares:
    {anchor_key, signature_hash, kind, file, qualified_name}."""
    out: List[Dict[str, Any]] = []
    for f in parsed_results:
        fp = f.get("file_path", "")
        for kind in CODE_KINDS:
            for el in _elements_of(kind, f):
                nm = el.get("name")
                if nm is None:
                    continue
                cn = el.get("class_name")
                out.append({
                    "anchor_key": anchor_key(kind, fp, nm, cn),
                    "signature_hash": _sig_hash(kind, el),
                    "kind": kind,
                    "file": fp,
                    "qualified_name": f"{cn}.{nm}" if kind == "method" and cn else nm,
                })
    return out


def _split_anchor(ak: str) -> Dict[str, str]:
    """kind:file:qualified_name → parts, for reporting deleted anchors whose
    element dict no longer exists. Best-effort (a path could contain ':')."""
    kind, _, rest = ak.partition(":")
    file, _, qname = rest.rpartition(":")
    return {"kind": kind, "file": file, "qualified_name": qname}


def compute_diff(
    old_state: Dict[str, List[str]],
    new_elements: List[Dict[str, Any]],
    first_parse: bool = False,
) -> DiffReport:
    """Compare the previous {anchor: [hash]} snapshot against the new parse.

      anchor only in new                     → ADDED
      anchor in both, hash multisets differ  → CHANGED
      anchor only in old                     → DELETED
    """
    report = DiffReport(first_parse=first_parse)

    new_by_anchor: Dict[str, List[Dict[str, Any]]] = {}
    for e in new_elements:
        new_by_anchor.setdefault(e["anchor_key"], []).append(e)

    for ak, els in new_by_anchor.items():
        entry = {
            "anchor_key": ak,
            "kind": els[0]["kind"],
            "file": els[0]["file"],
            "qualified_name": els[0]["qualified_name"],
        }
        new_hashes = sorted(e["signature_hash"] for e in els)
        old_hashes = old_state.get(ak)
        if old_hashes is None:
            report.added.append({**entry, "signature_hash": new_hashes[-1]})
        elif sorted(old_hashes) != new_hashes:
            report.changed.append({
                **entry,
                "old_signature": sorted(old_hashes)[-1],
                "signature_hash": new_hashes[-1],
            })

    for ak in old_state:
        if ak not in new_by_anchor:
            report.deleted.append({"anchor_key": ak, **_split_anchor(ak)})

    return report
