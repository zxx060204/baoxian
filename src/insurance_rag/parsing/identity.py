"""Explicit, document-version-bound identity review; no fuzzy merging."""
from .common import read_json


def load_identity_mapping(file):
    if file is None:
        return {}
    data = read_json(file)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError("invalid_identity_mapping_schema")
    result = {}
    for row in data["entries"]:
        if not isinstance(row, dict) or not row.get("document_version_id"):
            raise ValueError("invalid_identity_mapping_entry")
        key = row["document_version_id"]
        if key in result:
            raise ValueError("duplicate_identity_mapping")
        if row.get("status") not in ("verified", "conflict", "pending_review"):
            raise ValueError("invalid_identity_mapping_status")
        if row.get("status") == "verified":
            for field in ("product_id", "product_version_id", "reviewer", "reviewed_at", "evidence"):
                if not row.get(field):
                    raise ValueError("missing_mapping_" + field)
        result[key] = row
    return result


def reviewed_identity(document_version_id, scope, mapping):
    if scope == "company":
        return dict(product_id=None, product_version_id=None, linkage_status="not_applicable",
                    review_reasons=["company_material_requires_explicit_product_linkage"], identity_audit={})
    row = mapping.get(document_version_id)
    if not row:
        return dict(product_id=None, product_version_id=None, linkage_status="pending_review",
                    review_reasons=["product_version_not_verified"], identity_audit={})
    verified = row["status"] == "verified"
    return dict(product_id=row.get("product_id") if verified else None,
                product_version_id=row.get("product_version_id") if verified else None,
                linkage_status=row["status"], review_reasons=[] if verified else ["identity_" + row["status"]],
                identity_audit={k: row[k] for k in ("reviewer", "reviewed_at", "evidence") if k in row})
