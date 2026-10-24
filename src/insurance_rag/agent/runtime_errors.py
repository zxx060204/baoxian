"""Typed runtime failures; exception details never cross the worker boundary."""
from insurance_rag.agent.store import Conflict


class RuntimeSourceChanged(Conflict):
    """The running interpreter no longer matches its frozen source manifest."""


class RuntimeBindingMismatch(Conflict):
    """A run belongs to a different immutable runtime contract."""
