"""Default product taxonomy and compliance keywords.

Product classes carry behaviour attributes (cold chain, serialization, ...) that
drive requirement variants. Generic starting point; meant to be extended.
"""
from typing import Dict, List

PRODUCT_CLASSES: List[Dict] = [
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


def match_classes(text: str) -> List[str]:
    t = text.lower()
    return [c["name"] for c in PRODUCT_CLASSES if any(k in t for k in c["keywords"])]


def match_compliance(text: str) -> List[str]:
    t = text.lower()
    return [tag for tag, kws in COMPLIANCE_KEYWORDS.items() if any(k in t for k in kws)]
