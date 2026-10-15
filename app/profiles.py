"""Target-platform profiles: shape story vocabulary for the application being built."""
from typing import Dict

PLATFORMS: Dict[str, Dict[str, str]] = {
    "generic": {
        "label": "Generic business application",
        "guidance": "Write platform-neutral stories.",
    },
    "salesforce": {
        "label": "Salesforce",
        "guidance": (
            "Target Salesforce. Reference objects, fields, record types, flows, permission sets, "
            "approval processes, CPQ and integration points where relevant. Prefer declarative "
            "configuration; mention code only when unavoidable. Add platform items to labels."
        ),
    },
    "sap": {
        "label": "SAP S/4HANA",
        "guidance": (
            "Target SAP S/4HANA. Reference modules (MM, SD, FI, WM/EWM, QM), master data, material "
            "types, batch/serial profiles, interfaces (IDoc/API), batch jobs and authorizations."
        ),
    },
    "oracle": {
        "label": "Oracle ERP",
        "guidance": (
            "Target Oracle ERP Cloud. Reference modules (SCM, Financials, Procurement, Inventory), "
            "item master, setups, integrations (REST/FBDI) and security roles."
        ),
    },
    "dynamics": {
        "label": "Microsoft Dynamics 365",
        "guidance": (
            "Target Dynamics 365. Reference apps (Sales, Supply Chain, Finance), entities, workflows, "
            "Power Automate flows, security roles and data entities."
        ),
    },
    "crm": {
        "label": "CRM (other)",
        "guidance": "Target a CRM platform. Reference accounts, contacts, opportunities, cases and workflows.",
    },
    "servicenow": {
        "label": "ServiceNow",
        "guidance": "Target ServiceNow. Reference tables, catalog items, flows, ACLs and integrations.",
    },
}
