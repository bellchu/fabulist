"""Default product taxonomy and compliance keywords.

Product classes carry behaviour attributes (cold chain, serialization, ...) that
drive requirement variants. Generic starting point; meant to be extended.
"""
import threading
import time
from typing import Any, Dict, List, Tuple

DEFAULT_CLASSES: List[Dict] = [
    {"name": "Prescription pharmaceuticals", "attributes": ["serialized", "lot_expiry_tracked", "rx_required"],
     "keywords": ["prescription", "rx", "pharmaceutical", "drug", "generic", "brand"]},
    {"name": "Controlled substances", "attributes": ["controlled_substance", "serialized", "lot_expiry_tracked", "licensed_only"],
     "keywords": ["controlled", "schedule ii", "schedule 2", "narcotic", "opioid", "csos"]},
    {"name": "Biologics and vaccines", "attributes": ["cold_chain", "lot_expiry_tracked", "recall_sensitive"],
     "keywords": ["biologic", "vaccine", "cold chain", "refrigerat", "frozen", "temperature"]},
    {"name": "Sterile and IV products", "attributes": ["sterile", "lot_expiry_tracked"],
     "keywords": ["sterile", "iv ", "intravenous", "infusion", "compounded"]},
    {"name": "Medical-surgical supplies", "attributes": ["udi_tracked", "uom_hierarchy"],
     "keywords": ["med-surg", "surgical", "supplies", "gloves", "syringe", "catheter", "dressing"]},
    {"name": "Implants and devices", "attributes": ["udi_tracked", "serial_tracked", "recall_sensitive"],
     "keywords": ["implant", "device", "udi", "orthopedic", "stent", "pacemaker"]},
    {"name": "Kits and trays", "attributes": ["bom_components", "uom_hierarchy"],
     "keywords": ["kit", "tray", "bundle", "convenience pack"]},
    {"name": "Laboratory and diagnostics", "attributes": ["lot_expiry_tracked", "temperature_sensitive"],
     "keywords": ["laboratory", "lab ", "diagnostic", "reagent", "assay", "test kit"]},
    {"name": "Durable medical equipment", "attributes": ["serial_tracked", "returnable", "rental_or_service"],
     "keywords": ["dme", "durable", "wheelchair", "equipment", "rental"]},
    {"name": "OTC and consumer health", "attributes": ["uom_hierarchy"],
     "keywords": ["otc", "over-the-counter", "consumer health", "vitamin"]},
]

COMPLIANCE_KEYWORDS: Dict[str, List[str]] = {
    "HIPAA": ["phi", "patient", "hipaa", "protected health", "medical record"],
    "DSCSA": ["dscsa", "serializ", "track and trace", "traceab", "t3", "pedigree"],
    "21CFR11": ["21 cfr part 11", "electronic signature", "e-signature", "audit trail"],
    "DEA": ["dea", "controlled substance", "csos", "form 222", "schedule ii"],
    "SOX": ["sox", "sarbanes", "financial control", "segregation of duties", "journal entry"],
    "GxP": ["gxp", "gmp", "validation", "iq/oq", "quality management"],
    "UDI": ["udi", "unique device identification"],
}

COMPLIANCE_AC: Dict[str, str] = {
    "HIPAA": "PHI is access-controlled, encrypted in transit and at rest, and access is logged.",
    "DSCSA": "Transaction history, information and statement are captured and retrievable per lot/serial.",
    "21CFR11": "Changes are captured in a tamper-evident audit trail with user, timestamp and reason.",
    "DEA": "Controlled-substance handling enforces licensing checks and a complete chain of custody.",
    "SOX": "Financially relevant actions enforce segregation of duties and are auditable.",
    "GxP": "Functionality is covered by documented validation evidence before release.",
    "UDI": "Device identifiers are captured and stored per unit where required.",
}


def match_compliance(text: str) -> List[str]:
    t = text.lower()
    return [tag for tag, kws in COMPLIANCE_KEYWORDS.items() if any(k in t for k in kws)]

# Acceptance-criteria variants driven by product behaviour attributes.
DEFAULT_ATTRIBUTE_AC: Dict[str, str] = {
    "cold_chain": "Temperature excursions are captured and block allocation or shipment until dispositioned.",
    "controlled_substance": "Only licensed, authorized parties can order; quantities are checked against limits.",
    "serialized": "Product identifiers (GTIN, serial, lot, expiry) are captured at each custody change.",
    "lot_expiry_tracked": "Lot and expiry are recorded; expired or short-dated stock is blocked per policy.",
    "sterile": "Sterility and packaging integrity checks are recorded before release.",
    "udi_tracked": "UDI is captured and retrievable for each unit or lot.",
    "bom_components": "Kit components are validated, and component recalls propagate to the kit.",
    "recall_sensitive": "Affected lots can be identified and quarantined within the recall SLA.",
}


# ---- live taxonomy (admin-managed, cached) ---------------------------------

_cache: Dict[str, Any] = {"at": 0.0, "classes": None, "rules": None, "gen": 0}
_lock = threading.Lock()
_TTL = 30.0


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0
        _cache["gen"] += 1


def _load() -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    from . import config, db  # local: keeps this module importable without a database

    if not config.DATABASE_URL:
        return DEFAULT_CLASSES, DEFAULT_ATTRIBUTE_AC
    with _lock:
        if _cache["classes"] is not None and time.monotonic() - _cache["at"] < _TTL:
            return _cache["classes"], _cache["rules"]
        gen = _cache["gen"]
    classes = db.rows("SELECT id, name, attributes, keywords, description FROM product_classes ORDER BY name")
    rules = {r["attribute"]: r["criteria"] for r in db.rows("SELECT attribute, criteria FROM attribute_rules")}
    with _lock:
        # An invalidate() that ran while we were reading means this result may predate the write: don't cache it.
        if _cache["gen"] == gen:
            _cache.update(at=time.monotonic(), classes=classes, rules=rules)
    return classes, rules


def classes() -> List[Dict[str, Any]]:
    return _load()[0]


def attribute_rules() -> Dict[str, str]:
    return _load()[1]


def match_classes(text: str) -> List[str]:
    t = text.lower()
    return [c["name"] for c in classes() if any(k in t for k in c["keywords"])]


def variant_criteria(class_names: List[str]) -> List[Dict[str, str]]:
    rules = attribute_rules()
    out, seen = [], set()
    for c in classes():
        if c["name"] not in class_names:
            continue
        for attr in c["attributes"]:
            if attr in rules and attr not in seen:
                seen.add(attr)
                out.append({"scope": c["name"], "criteria": rules[attr]})
    return out


def seed() -> None:
    """First start: load the built-in medical-distribution taxonomy."""
    import json

    from . import db

    with db.conn() as c:
        if c.execute("SELECT 1 FROM product_classes LIMIT 1").fetchone() is None:
            for d in DEFAULT_CLASSES:
                c.execute("INSERT INTO product_classes (id, name, attributes, keywords, description, created) VALUES (%s,%s,%s,%s,'',%s)",
                          (db.new_id(), d["name"], json.dumps(d["attributes"]), json.dumps(d["keywords"]), db.now()))
        if c.execute("SELECT 1 FROM attribute_rules LIMIT 1").fetchone() is None:
            for attr, criteria in DEFAULT_ATTRIBUTE_AC.items():
                c.execute("INSERT INTO attribute_rules (attribute, criteria) VALUES (%s,%s)", (attr, criteria))
    invalidate()
