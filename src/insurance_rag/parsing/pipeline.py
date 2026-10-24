"""Isolated documents, integrity-checked caching, immutable parse attempts."""
from collections import Counter
from pathlib import Path
import json
import os

from .common import file_hash, read_json, relative_path, stable_hash, timestamp, workspace_path, write_json, write_jsonl, atomic_text, filesystem_path
from .models import ManifestEntry, ParseConfig, PARSER_VERSION, SCHEMA_VERSION
from .native import engine_versions, extract_document
from .validation import validate_layout
from .excel import extract_workbook,excel_versions

ARTIFACTS = ("layout.json", "content.md", "tables.json", "parse_report.json", "provenance.json",
             "review_candidates.jsonl")


def _markdown(entry, layout):
    lines = ["# " + (entry.product_name or entry.source_category), "",
             "文档版本：" + entry.document_version_id, "",
             "文档解析结果；业务内容尚未完成验证。", ""]
    by_page = {}
    for block in layout["blocks"]:
        if block["block_type"] != "image":
            by_page.setdefault(block["page_number"], []).append(block)
    for page in layout["pages"]:
        if page["status"] == "not_processed_sample":
            continue
        lines += [f"## 第 {page['page_number']} 页", "", f"处理状态：{page['status']}", ""]
        for block in by_page.get(page["page_number"], []):
            lines += [block["normalized_text"], ""]
    return "\n".join(lines) + "\n"


def _cached(base, manifest_hash):
    base = filesystem_path(base)
    marker = base / "latest.json"
    if not marker.exists():
        return None
    try:
        data = read_json(marker)
        if data["manifest_hash"] != manifest_hash or data["status"] not in ("completed", "completed_with_warnings", "unsupported_format"):
            return None
        attempt_name = data["attempt"]
        if not isinstance(attempt_name, str) or not attempt_name.startswith("attempt_") or "/" in attempt_name or "\\" in attempt_name:
            return None
        folder = base / attempt_name
        commit = read_json(folder / "complete.json")
        if commit.get("manifest_hash") != manifest_hash or set(commit["artifact_hashes"]) != set(ARTIFACTS):
            return None
        if any(file_hash(folder / name) != digest for name, digest in commit["artifact_hashes"].items()):
            return None
        for name, digest in commit.get("render_hashes", {}).items():
            candidate = (folder / name).resolve()
            if not candidate.is_relative_to(folder.resolve()) or file_hash(candidate) != digest:
                return None
        return folder, read_json(folder / "parse_report.json")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parse_document(manifest_entry, config=None, *, workspace_root, output="data/parsed", force=False):
    entry = manifest_entry if isinstance(manifest_entry, ManifestEntry) else ManifestEntry.from_dict(manifest_entry)
    config = config or ParseConfig()
    workspace = Path(workspace_root).resolve(strict=True)
    output = workspace_path(output, workspace)
    versions = engine_versions(config)
    if entry.file_type in ('xlsx','xls'):versions.update(excel_versions())
    parse_key = stable_hash(["parse-v1", entry.file_sha256, PARSER_VERSION, versions,
                             config.to_dict(), SCHEMA_VERSION])
    manifest_hash = stable_hash(entry.to_dict())
    base = output / entry.document_version_id / parse_key
    base = filesystem_path(workspace_path(base, workspace))
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "active.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("parse_task_locked; verify previous process before removing stale lock") from exc
    with os.fdopen(descriptor, "w", encoding="utf8") as stream:
        json.dump(dict(pid=os.getpid(), started_at=timestamp()), stream)
    try:
        # Always hash the input before cache lookup, including the unsupported-format path.
        file = workspace_path(entry.file_path, workspace, must_exist=True)
        if file_hash(file) != entry.file_sha256:
            raise ValueError("file_hash_mismatch")
        if not force:
            cached = _cached(base, manifest_hash)
            if cached:
                folder, report = cached
                return dict(document_version_id=entry.document_version_id, parse_key=parse_key,
                            status=report["status"], cached=True, output=relative_path(folder, workspace),
                            publication_status=report["publication_status"])
        number = 1
        while (base / f"attempt_{number:04d}").exists():
            number += 1
        folder = base / f"attempt_{number:04d}"
        folder.mkdir()
        started = timestamp()
        errors = []
        if entry.file_type not in ("pdf","xlsx","xls"):
            layout = dict(page_count=None, pages=[], blocks=[], sections=[], tables=[], errors=[])
            status = "unsupported_format"
            reasons = ["unsupported_format"]
        else:
            try:
                layout = (extract_workbook(file,config,parse_key) if entry.file_type in ('xlsx','xls') else
                          extract_document(file, config, parse_key, artifact_dir=folder, document_type=entry.document_type))
                errors = layout.pop("errors", [])
                try:
                    quality_reasons = validate_layout(layout)
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append(dict(error_type=type(exc).__name__, message=str(exc), stage="structural_validation"))
                    quality_reasons = ["structural_validation_failed"]
                reasons = sorted(set(quality_reasons) | {r for p in layout["pages"] for r in p.get("review_reasons", [])})
                status = "failed" if errors else ("completed_with_warnings" if reasons else "completed")
            except Exception as exc:
                layout = dict(page_count=None, pages=[], blocks=[], sections=[], tables=[])
                status, errors, reasons = "failed", [dict(error_type=type(exc).__name__, message=str(exc))], ["document_extraction_failed"]
        if file_hash(file) != entry.file_sha256:
            status = "failed"
            errors.append("input_changed_during_parse")
            reasons.append("input_changed_during_parse")
        reasons = sorted(set(reasons + entry.review_reasons + ["parsed_content_not_business_validated"]))
        if entry.document_type in ("premium_rate", "cash_value"):
            reasons.append("financial_table_semantics_not_verified" if config.extract_tables else "financial_table_extraction_not_implemented")
        if entry.linkage_status != "verified":
            reasons.append("product_version_not_verified")
        from .table_evidence import partial_report
        report = dict(partial_tables=partial_report(layout),schema_version=1, document_version_id=entry.document_version_id, parse_key=parse_key,
                      parser_version=PARSER_VERSION, engine_versions=versions, config=config.to_dict(),
                      started_at=started, completed_at=timestamp(), status=status,
                      page_count=layout["page_count"], page_status_count=len(layout["pages"]),
                      processed_page_count=sum(p["status"] not in ("not_processed_limit", "not_processed_sample") for p in layout["pages"]),
                      page_statuses=dict(Counter(p["status"] for p in layout["pages"])),
                      errors=errors, review_reasons=sorted(set(reasons)),
                      publication_status="quarantined", linkage_status=entry.linkage_status)
        layout.update(schema_version=1, parser_version=PARSER_VERSION, document_id=entry.document_id,
                      document_version_id=entry.document_version_id, parse_key=parse_key,
                      metadata={k: entry.to_dict()[k] for k in ("company_id", "source_product_key",
                                "product_name", "product_id", "product_version_id", "document_type", "scope")})
        for block in layout["blocks"]:
            block["original_reference"].update(document_version_id=entry.document_version_id,
                                                parse_key=parse_key, file_path=entry.file_path)
        for table in layout["tables"]:
            table["original_reference"] = dict(document_version_id=entry.document_version_id, parse_key=parse_key,
                                               file_path=entry.file_path, page_number=table["page_number"])
            for cell in table["cells"]:
                cell["original_reference"].update(table["original_reference"])
        write_json(folder / "layout.json", layout)
        atomic_text(folder / "content.md", _markdown(entry, layout))
        write_json(folder / "tables.json", dict(schema_version=1, document_version_id=entry.document_version_id,
                                               tables=layout["tables"], extraction_status="completed_candidates" if config.extract_tables else "not_implemented"))
        write_json(folder / "parse_report.json", report)
        write_json(folder / "provenance.json", dict(manifest=entry.to_dict(), manifest_hash=manifest_hash,
                                                    parser_version=PARSER_VERSION, engine_versions=versions,
                                                    config=config.to_dict()))
        write_jsonl(folder / "review_candidates.jsonl",
                    [dict(document_version_id=entry.document_version_id, reason=reason) for reason in report["review_reasons"]])
        if status != "failed":
            write_json(folder / "complete.json", dict(manifest_hash=manifest_hash,
                       artifact_hashes={name: file_hash(folder / name) for name in ARTIFACTS},
                       render_hashes={f.relative_to(folder).as_posix(): file_hash(f)
                                      for f in (folder / "renders").glob("*.png")}))
        write_json(base / "latest.json", dict(attempt=folder.name, manifest_hash=manifest_hash, status=status))
        return dict(document_version_id=entry.document_version_id, parse_key=parse_key, status=status,
                    cached=False, output=relative_path(folder, workspace), publication_status="quarantined")
    finally:
        lock.unlink(missing_ok=True)


def parse_manifest(manifest, workspace_root, *, output="data/parsed", config=None, force=False,
                   document_version_id=None, retry_failed=False):
    workspace = Path(workspace_root).resolve(strict=True)
    manifest = workspace_path(manifest, workspace, must_exist=True)
    output = workspace_path(output, workspace)
    results, entries, rejected, seen, duplicate_keys = [], [], [], {}, set()
    with manifest.open(encoding="utf-8-sig") as stream:
        for index, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                entry = ManifestEntry.from_dict(json.loads(line))
                key = entry.document_version_id
                if key in seen:
                    duplicate_keys.add(key)
                    raise ValueError("duplicate_document_version_in_manifest")
                seen[key] = index
                entries.append((index, entry))
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append(dict(line=index, status="failed_input", error=str(exc)))
    if not entries and not rejected:
        rejected.append(dict(status="failed_input", error="empty_manifest"))
    for index, entry in entries:
        if entry.document_version_id in duplicate_keys:
            rejected.append(dict(line=index, status="failed_input", error="duplicate_document_version_in_manifest"))
            continue
        if document_version_id and entry.document_version_id != document_version_id:
            continue
        try:
            # retry_failed limits the batch to the last failed attempts.
            if retry_failed:
                versions = engine_versions(config)
                key = stable_hash(["parse-v1", entry.file_sha256, PARSER_VERSION, versions,
                                   (config or ParseConfig()).to_dict(), SCHEMA_VERSION])
                marker = output / entry.document_version_id / key / "latest.json"
                if not marker.exists() or read_json(marker).get("status") != "failed":
                    continue
            results.append(dict(line=index, **parse_document(entry, config, workspace_root=workspace,
                                                           output=output, force=force)))
        except Exception as exc:
            results.append(dict(line=index, document_version_id=entry.document_version_id,
                                status="failed", error=str(exc), error_type=type(exc).__name__))
    if document_version_id and not any(e.document_version_id == document_version_id for _, e in entries):
        rejected.append(dict(status="failed_input", error="document_version_id_not_found"))
    report = dict(schema_version=1, generated_at=timestamp(), manifest=relative_path(manifest, workspace),
                  counts=dict(Counter(r["status"] for r in results + rejected)), results=results, rejected=rejected)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / ("batch_report_" + stable_hash([timestamp(), os.getpid()]) + ".json"), report)
    return report
