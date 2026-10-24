"""Adapt checkpoint/summary records to verified-file manifests."""
from collections import Counter
from pathlib import Path
import json

from .common import file_hash, read_json, relative_path, stable_hash, timestamp, workspace_path, write_json, write_jsonl
from .identity import load_identity_mapping, reviewed_identity
from .models import ManifestEntry, require_hash

DOCUMENT_TYPES = {"条款": "terms", "产品说明书": "brochure", "费率表": "premium_rate", "现金价值表": "cash_value"}


def read_checkpoint(file, recover_tail=False):
    text = Path(file).read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    latest, warnings = {}, []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if recover_tail and index == len(lines) and not line.endswith(("\n", "\r")):
                warnings.append(dict(reason="incomplete_checkpoint_tail", line=index))
                continue
            raise ValueError(f"invalid_checkpoint_json:{file}:{index}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError(f"invalid_checkpoint_record:{file}:{index}")
        latest[row["id"]] = (row, index)
    return latest, warnings


def company_records(folder, recover_tail=False):
    checkpoint, summary = folder / "checkpoint.jsonl", folder / "summary.json"
    warnings = []
    if checkpoint.exists():
        latest, warnings = read_checkpoint(checkpoint, recover_tail)
        if summary.exists():
            data = read_json(summary)
            rows = data.get("records") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError("invalid_summary_records")
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    raise ValueError("invalid_summary_record")
                current = latest.get(row["id"])
                if not current or current[0] != row:
                    warnings.append(dict(reason="summary_checkpoint_difference", record_id=row["id"]))
        return [(row, checkpoint, line) for row, line in latest.values()], warnings
    if not summary.exists():
        return [], [dict(reason="no_checkpoint_or_summary")]
    data = read_json(summary)
    rows = data.get("records") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("invalid_summary_records")
    latest = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("invalid_summary_record")
        if row["id"] in latest:
            raise ValueError("duplicate_summary_record")
        latest[row["id"]] = (row, summary, index)
    return list(latest.values()), warnings


def _entry(row, item, index, source_file, source_line, run, workspace, mapping):
    company = row.get("company")
    identity = row.get("product_identity")
    if not isinstance(company, str) or not company or not isinstance(identity, str) or not identity:
        raise ValueError("missing_source_identity")
    if not isinstance(row.get("category"), str) or not row["category"]:
        raise ValueError("missing_category")
    path = workspace_path(item["file"], workspace, must_exist=True)
    require_hash(item.get("sha256"), "file_sha256")
    if file_hash(path) != item["sha256"]:
        raise ValueError("file_hash_mismatch")
    file_type = item.get("file_type")
    if not isinstance(file_type, str) or not file_type:
        raise ValueError("missing_file_type")
    parent = None
    if row.get("raw_file"):
        parent = workspace_path(row["raw_file"], workspace, must_exist=True)
        require_hash(row.get("sha256"), "parent_sha256")
        if file_hash(parent) != row["sha256"]:
            raise ValueError("parent_hash_mismatch")
    member = item.get("member")
    if member in (None, "direct"):
        member = None
    elif not isinstance(member, str):
        raise ValueError("invalid_archive_member")
    member_key = [member, index] if member is not None else ["direct", index]
    document_id = stable_hash(["document-v1", company, row["id"], member_key])
    version_id = stable_hash(["document-version-v1", document_id, item["sha256"]])
    scope = row.get("scope", "product")
    review = reviewed_identity(version_id, scope, mapping)
    if row.get("product_label_complete") is False:
        review["review_reasons"].append("incomplete_product_label")
    return ManifestEntry(
        company_id=company, source_record_id=row["id"], source_run_id=relative_path(run, workspace),
        source_product_identity=identity,
        source_product_key=stable_hash(["source-product-v1", company, identity]),
        document_id=document_id, document_version_id=version_id,
        document_type=DOCUMENT_TYPES.get(row["category"], "other"), source_category=row["category"],
        file_path=relative_path(path, workspace), file_type=file_type.lower(), file_sha256=item["sha256"],
        product_name=row.get("product_name", ""), scope=scope,
        source_reference=dict(file=relative_path(source_file, workspace), line_or_index=source_line),
        source_row=row.get("source_row") or {}, source_url=row.get("url"),
        final_url=row.get("final_url") or row.get("source_final_url"), source_catalog=row.get("source_catalog"),
        parent_raw_file=relative_path(parent, workspace) if parent else None,
        parent_sha256=row.get("sha256") if parent else None, archive_member=member, member_index=index,
        source_version_evidence={k: row[k] for k in ("versionNo", "product_code", "version_or_date",
                                                    "filing_code", "body_version_status") if k in row},
        source_updated_at=row.get("updated_at"), download_time=row.get("download_time"),
        validation_level=row.get("validation_level"), pdf_validation=item.get("pdf_validation") or {},
        **review,
    )


def build_manifest(acquisition_run, workspace_root, identity_mapping=None, *, recover_tail=False):
    workspace = Path(workspace_root).resolve(strict=True)
    run = workspace_path(acquisition_run, workspace, must_exist=True)
    if not run.is_dir():
        raise ValueError("input_must_be_directory")
    if (run / "active.lock").exists() or (run.parent / "active.lock").exists():
        raise ValueError("acquisition_run_active")
    mapping = load_identity_mapping(workspace_path(identity_mapping, workspace, must_exist=True)) if identity_mapping else {}
    folders = [run] if any((run / n).exists() for n in ("checkpoint.jsonl", "summary.json")) else [
        p for p in sorted(run.iterdir()) if p.is_dir()
        and any((p / n).exists() for n in ("checkpoint.jsonl", "summary.json"))
    ]
    if not folders:
        raise ValueError("no_acquisition_records_found")
    documents, excluded, warnings, seen = [], [], [], set()
    for folder in folders:
        workspace_path(folder, workspace, must_exist=True)
        if (folder / "active.lock").exists():
            raise ValueError("acquisition_company_active")
        records, notices = company_records(folder, recover_tail)
        warnings.extend(dict(company_folder=relative_path(folder, workspace), **n) for n in notices)
        catalog = read_json(folder / "catalog.json") if (folder / "catalog.json").exists() else {}
        if not isinstance(catalog, dict) or not isinstance(catalog.get("candidates", []), list):
            raise ValueError("invalid_catalog")
        candidates = {}
        for candidate in catalog.get("candidates", []):
            if isinstance(candidate, dict) and candidate.get("id"):
                candidates[candidate["id"]] = candidate
        for saved, source_file, source_line in records:
            row = {**candidates.get(saved["id"], {}), **saved}
            base = dict(source_record_id=row["id"], source_reference=relative_path(source_file, workspace),
                        line_or_index=source_line, company=row.get("company"), acquisition_status=row.get("status"))
            if row.get("status") != "validated":
                excluded.append(dict(base, reason="acquisition_not_validated", failure_kind=row.get("failure_kind")))
                continue
            items = row.get("files")
            if not isinstance(items, list) or not items:
                excluded.append(dict(base, reason="validated_record_has_no_files"))
                continue
            for index, item in enumerate(items):
                try:
                    if not isinstance(item, dict):
                        raise ValueError("invalid_file_record")
                    entry = _entry(row, item, index, source_file, source_line, run, workspace, mapping)
                    if entry.document_version_id in seen:
                        raise ValueError("duplicate_document_version")
                    seen.add(entry.document_version_id)
                    documents.append(entry)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    excluded.append(dict(base, member_index=index, reason=str(exc), error_type=type(exc).__name__))
    return dict(
        schema_version=1, generated_at=timestamp(), source_run_id=relative_path(run, workspace),
        documents=[d.to_dict() for d in documents], excluded=excluded, warnings=warnings,
        counts=dict(documents=len(documents), excluded=len(excluded), warnings=len(warnings)),
        by_format=dict(Counter(d.file_type for d in documents)),
        by_document_type=dict(Counter(d.document_type for d in documents)),
    )


def export_manifest(result, output, workspace_root):
    folder = workspace_path(output, workspace_root)
    folder.mkdir(parents=True, exist_ok=True)
    # Remove the commit marker before replacing members; partial writes cannot look complete.
    marker = folder / "manifest_report.json"
    marker.unlink(missing_ok=True)
    write_jsonl(folder / "documents.jsonl", result["documents"])
    write_jsonl(folder / "excluded.jsonl", result["excluded"])
    report = {k: v for k, v in result.items() if k not in ("documents", "excluded")}
    report["artifact_hashes"] = {name: file_hash(folder / name) for name in ("documents.jsonl", "excluded.jsonl")}
    write_json(marker, report)
    return report
