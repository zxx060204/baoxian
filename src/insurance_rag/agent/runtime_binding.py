"""Freeze native runtime sources before loading its engine; never hash legacy graphs."""
from pathlib import Path
from insurance_rag.parsing.common import file_hash, stable_hash
from insurance_rag.agent.runtime_errors import RuntimeSourceChanged

AGENT_FILES = (
    '__init__.py', 'conditions.py', 'presentation.py',
    'context.py', 'output_validation.py', 'engine.py', 'loop_bootstrap.py', 'loop_config.py',
    'loop_store.py', 'loop_artifacts.py', 'memory.py', 'profile.py', 'local_content.py', 'models.py',
    'model_adapter.py', 'store.py', 'supervisor.py', 'runtime_binding.py',
    'runtime_errors.py', 'worker_protocol.py',
)
SHARED_PACKAGES = ('illustration', 'ingestion', 'onboarding', 'tools', 'services', 'retrieval', 'indexing', 'parsing',
                   'reliability', 'workspace')
ENTRY_FILES = ('src/insurance_rag/runtime_env.py',
               'src/insurance_rag/workflow/store.py',
               'src/insurance_rag/api/app.py',
               'src/insurance_rag/api/product_ingestion.py',
               'scripts/product_ingestion_worker.py',
               'scripts/agent_worker.py', 'scripts/serve_agent.py',
               'requirements-agent-loop.lock')


def source_manifest(root):
    root = Path(root).resolve()
    paths = {root / 'src/insurance_rag/agent' / name for name in AGENT_FILES}
    paths.update(root / name for name in ENTRY_FILES)
    for package in SHARED_PACKAGES:
        paths.update((root / 'src/insurance_rag' / package).rglob('*.py'))
    return {p.relative_to(root).as_posix(): file_hash(p)
            for p in sorted(paths) if p.is_file()}


class RuntimeSnapshot:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = source_manifest(self.root)

    def assert_current(self):
        if source_manifest(self.root) != self.manifest:
            raise RuntimeSourceChanged('runtime_source_changed_restart_required')

    def binding(self, mode):
        self.assert_current()
        return stable_hash({'manifest_version': 2, 'files': self.manifest, 'mode': mode})


# loop_bootstrap imports this module BEFORE importing AgentEngine.
BOOT_SNAPSHOT = RuntimeSnapshot(Path(__file__).resolve().parents[3])
