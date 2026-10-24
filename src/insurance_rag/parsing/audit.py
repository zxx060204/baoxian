"""Shared annotation audit and geometry checks, independent of business mapping."""
from datetime import datetime
import math
def reviewed(audit):
    if not isinstance(audit, dict) or audit.get("status") != "confirmed":
        return False
    if not isinstance(audit.get("reviewer"), str) or not audit["reviewer"].strip():
        return False
    try:
        date = datetime.fromisoformat(audit["reviewed_at"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    evidence = audit.get("evidence")
    return isinstance(evidence, list) and bool(evidence) and all(
        isinstance(item, str) and item.strip() for item in evidence)



def valid_bbox(box):
    return (isinstance(box, list) and len(box) == 4 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in box) and box[0] < box[2] and box[1] < box[3])


