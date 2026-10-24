"""Versioned manifests and conservative native parser configuration."""
from dataclasses import asdict, dataclass, field, fields
import re

from .common import stable_hash

SCHEMA_VERSION = 1
PARSER_VERSION = "layout-ocr-0.4.1"


def require_hash(value, name):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid_" + name)


@dataclass
class ManifestEntry:
    company_id: str
    source_record_id: str
    source_run_id: str
    source_product_identity: str
    source_product_key: str
    document_id: str
    document_version_id: str
    document_type: str
    source_category: str
    file_path: str
    file_type: str
    file_sha256: str
    product_name: str = ""
    scope: str = "product"
    schema_version: int = SCHEMA_VERSION
    id_algorithm: str = "sha256-json-v1"
    source_reference: dict = field(default_factory=dict)
    source_row: dict = field(default_factory=dict)
    source_url: str | None = None
    final_url: str | None = None
    source_catalog: str | None = None
    parent_raw_file: str | None = None
    parent_sha256: str | None = None
    archive_member: str | None = None
    member_index: int = 0
    source_version_evidence: dict = field(default_factory=dict)
    source_updated_at: str | None = None
    download_time: str | None = None
    acquisition_status: str = "validated"
    validation_level: str | None = None
    pdf_validation: dict = field(default_factory=dict)
    product_id: str | None = None
    product_version_id: str | None = None
    linkage_status: str = "pending_review"
    review_reasons: list = field(default_factory=list)
    identity_audit: dict = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.schema_version, bool) or self.schema_version != SCHEMA_VERSION or self.id_algorithm != "sha256-json-v1":
            raise ValueError("unsupported_manifest_schema")
        for name in ("source_reference", "source_row", "source_version_evidence", "pdf_validation", "identity_audit"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError("invalid_" + name)
        if not isinstance(self.review_reasons, list) or not all(isinstance(x, str) for x in self.review_reasons):
            raise ValueError("invalid_review_reasons")
        if not isinstance(self.product_name, str):
            raise ValueError("invalid_product_name")
        for name in ("company_id", "source_record_id", "source_run_id", "source_product_identity",
                     "file_path", "file_type", "source_category"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError("missing_" + name)
        for name in ("source_product_key", "document_id", "document_version_id", "file_sha256"):
            require_hash(getattr(self, name), name)
        if self.source_product_key != stable_hash(["source-product-v1", self.company_id, self.source_product_identity]):
            raise ValueError("source_product_key_mismatch")
        if self.document_type not in ("terms", "brochure", "premium_rate", "cash_value", "other"):
            raise ValueError("invalid_document_type")
        for name in ("source_url", "final_url", "source_catalog", "parent_raw_file", "archive_member",
                     "source_updated_at", "download_time", "product_id", "product_version_id", "validation_level"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("invalid_" + name)
        if self.scope not in ("product", "company"):
            raise ValueError("invalid_scope")
        if isinstance(self.member_index, bool) or not isinstance(self.member_index, int) or self.member_index < 0:
            raise ValueError("invalid_member_index")
        member_key = [self.archive_member, self.member_index] if self.archive_member is not None else ["direct", self.member_index]
        expected_id = stable_hash(["document-v1", self.company_id, self.source_record_id, member_key])
        if self.document_id != expected_id:
            raise ValueError("document_id_mismatch")
        if self.document_version_id != stable_hash(["document-version-v1", expected_id, self.file_sha256]):
            raise ValueError("document_version_id_mismatch")
        if self.acquisition_status != "validated":
            raise ValueError("manifest_not_validated")
        if self.linkage_status not in ("pending_review", "verified", "conflict", "not_applicable"):
            raise ValueError("invalid_linkage_status")
        if self.linkage_status == "verified":
            if self.scope != "product" or not self.product_id or not self.product_version_id:
                raise ValueError("invalid_verified_identity")
            for key in ("reviewer", "reviewed_at", "evidence"):
                expected_type = list if key == "evidence" else str
                if not isinstance(self.identity_audit.get(key), expected_type) or not self.identity_audit[key]:
                    raise ValueError("missing_identity_audit_" + key)
        elif self.product_id is not None or self.product_version_id is not None:
            raise ValueError("unverified_identity_has_canonical_ids")
        if self.parent_sha256 is not None:
            require_hash(self.parent_sha256, "parent_sha256")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("manifest_entry_must_be_object")
        unknown = set(value) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("unknown_manifest_fields: " + ", ".join(sorted(unknown)))
        return cls(**value)


@dataclass(frozen=True)
class ParseConfig:
    image_coverage_threshold: float = 0.15
    suspicious_char_ratio: float = 0.02
    table_edge_threshold: int = 8
    max_pages: int = 2000
    ocr_mode: str = "off"
    ocr_dpi: int = 180
    ocr_min_confidence: float = 0.8
    ocr_max_pixels: int = 20000000
    layout_mode: str = "native"
    extract_tables: bool = False
    page_selection: str = "all"
    page_numbers: tuple = ()
    page_checkpoint: bool = True

    def __post_init__(self):
        if not isinstance(self.page_numbers,(tuple,list)) or any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in self.page_numbers) or len(set(self.page_numbers))!=len(self.page_numbers):
            raise ValueError('invalid_page_numbers')
        object.__setattr__(self,'page_numbers',tuple(self.page_numbers))
        if not isinstance(self.page_checkpoint,bool):raise ValueError('invalid_page_checkpoint')
        if self.page_selection not in ("all", "first_last"):
            raise ValueError("invalid_page_selection")
        if self.ocr_mode not in ("off", "auto", "force") or self.layout_mode not in ("native", "xy_cut"):
            raise ValueError("invalid_engine_mode")
        if not isinstance(self.extract_tables, bool):
            raise ValueError("invalid_extract_tables")
        if not isinstance(self.ocr_dpi, int) or isinstance(self.ocr_dpi, bool) or not 72 <= self.ocr_dpi <= 600:
            raise ValueError("invalid_ocr_dpi")
        for value in (self.image_coverage_threshold, self.suspicious_char_ratio, self.ocr_min_confidence):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 < value <= 1:
                raise ValueError("invalid_ratio_threshold")
        for value in (self.table_edge_threshold, self.max_pages, self.ocr_max_pixels):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("invalid_positive_limit")

    def to_dict(self):
        return asdict(self)
