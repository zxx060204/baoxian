"""Small allowlisted worker result protocol, separate from arbitrary child output."""
import json
from insurance_rag.reliability.runtime import error_code

ERROR_CODES = frozenset((
    'runtime_source_changed_restart_required', 'runtime_binding_mismatch',
    'service_unavailable', 'rate_limited', 'dependency_unavailable',
    'authentication_failed', 'contract_or_quality_violation', 'internal_error',
    'deadline_exceeded', 'budget_exceeded', 'worker_startup_failed',
    'worker_protocol_invalid', 'worker_exited_without_completion',
))


def failure(exc, run_id, phase):
    code = error_code(exc)
    return {'run_id': run_id, 'state': 'failed',
            'error_code': code if code in ERROR_CODES else 'internal_error',
            'phase': phase}


def read_report(raw, run_id):
    try:
        if len(raw) > 4096:
            return None
        report = json.loads(raw)
        if (not isinstance(report, dict) or report.get('run_id') != run_id
                or set(report) - {'run_id', 'state', 'error_code', 'phase'}
                or report.get('state') not in ('completed', 'waiting', 'failed', 'cancelled')
                or report.get('error_code') is not None and report['error_code'] not in ERROR_CODES
                or report.get('phase') not in ('initialize', 'execute', 'complete')):
            return None
        return report
    except (ValueError, TypeError, UnicodeError):
        return None
