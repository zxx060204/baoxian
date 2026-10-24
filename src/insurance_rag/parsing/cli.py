"""Manifest and PDF/OCR CLI; outputs never imply business approval."""
import argparse
import json
from dataclasses import fields

from ..paths import get_project_root
from .common import read_json, workspace_path
from .manifest import build_manifest, export_manifest
from .models import ParseConfig
from .pipeline import parse_manifest


def _manifest_arguments(parser):
    parser.add_argument("--input", required=True, help="Acquisition run or company directory")
    parser.add_argument("--output", required=True, help="Manifest output directory")
    parser.add_argument("--workspace-root")
    parser.add_argument("--identity-mapping", help="Document-version-bound reviewed mapping JSON")
    parser.add_argument("--recover-tail", action="store_true", help="Report and ignore only an incomplete final checkpoint line")


def _parse_arguments(parser):
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="data/parsed")
    parser.add_argument("--workspace-root")
    parser.add_argument("--config", help="Version-independent JSON ParseConfig")
    parser.add_argument("--document-version-id")
    parser.add_argument("--force", action="store_true", help="Create a new immutable attempt")
    parser.add_argument("--retry-failed", action="store_true", help="Only retry documents with a prior failed attempt")


def _execute(args):
    workspace = args.workspace_root or get_project_root()
    if args.command == "manifest":
        result = build_manifest(args.input, workspace, args.identity_mapping, recover_tail=args.recover_tail)
        report = export_manifest(result, args.output, workspace)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if any("error_type" in r for r in result["excluded"]) else 0
    config = ParseConfig()
    if args.config:
        data = read_json(workspace_path(args.config, workspace, must_exist=True))
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(ParseConfig)}:
            raise ValueError("unknown_or_invalid_parse_config")
        config = ParseConfig(**data)
    report = parse_manifest(args.manifest, workspace, output=args.output, config=config,
                            force=args.force, retry_failed=args.retry_failed,
                            document_version_id=args.document_version_id)
    print(json.dumps(report, ensure_ascii=False))
    return 1 if any(report["counts"].get(key, 0) for key in ("failed", "failed_input")) else 0


def _run(parser, argv=None):
    args = parser.parse_args(argv)
    try:
        return _execute(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="failed", error=str(exc), error_type=type(exc).__name__), ensure_ascii=False))
        return 1


def manifest_main(argv=None):
    parser = argparse.ArgumentParser(description="Build source-traceable manifests from acquisition records")
    _manifest_arguments(parser)
    parser.set_defaults(command="manifest")
    return _run(parser, argv)


def parse_main(argv=None):
    parser = argparse.ArgumentParser(description="Parse all PDF pages with configurable native text, OCR, layout and tables")
    _parse_arguments(parser)
    parser.set_defaults(command="parse")
    return _run(parser, argv)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Insurance document parsing tools")
    sub = parser.add_subparsers(dest="command", required=True)
    _manifest_arguments(sub.add_parser("manifest"))
    _parse_arguments(sub.add_parser("parse"))
    return _run(parser, argv)
