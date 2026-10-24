"""Persistence projection for user-local files; full bodies are execution-only."""
import copy
import json
from insurance_rag.parsing.common import stable_hash

FILE_TOOLS = {'read_file', 'write_file', 'patch_file'}


def stored_file_result(value):
    out = copy.deepcopy(value)
    data = out.get('data', {})
    if data.get('path') and data.get('version'):
        body = data.pop('content', None)
        if body is not None:
            data['content_characters'] = len(body)
            data['content_retention'] = 'local_only_read_again_when_needed'
    return out


def stored_message(message):
    out = copy.deepcopy(message)
    for call in out.get('tool_calls', []):
        if call['function']['name'] not in ('write_file', 'patch_file'):
            continue
        try:
            args = json.loads(call['function']['arguments'])
        except (ValueError, TypeError):
            continue
        safe = {k: v for k, v in args.items() if k not in ('content', 'old', 'new')}
        safe.update(__local_content_omitted__=True, payload_hash=stable_hash(args))
        if 'content' in args:
            safe['file_hash'] = __import__('hashlib').sha256(args['content'].encode('utf8')).hexdigest()
        call['function']['arguments'] = json.dumps(safe, ensure_ascii=False)
    if out.get('role') == 'tool':
        try:
            value = json.loads(out.get('content', ''))
            out['content'] = json.dumps(stored_file_result(value), ensure_ascii=False)
        except (ValueError, TypeError, AttributeError):
            pass
    return out
