"""Business administration dependencies without loading the Agent runtime."""
import os
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from insurance_rag.runtime_env import load_env
from insurance_rag.services.config import AgentSettings,ChatSettings
from insurance_rag.agent.store import AgentStore
from insurance_rag.agent.models import DeepSeekChat
from insurance_rag.services.source_library import SourceLibraryTools,current_release
from insurance_rag.workflow.store import WorkflowStore


@contextmanager
def open_services(root=None,*,load_model=True,mode=None):
 root=Path(root or Path(__file__).resolve().parents[3]);load_env(root/'.env')
 settings=AgentSettings.from_environment()
 from insurance_rag.reliability.diagnostics import stage
 with stage(root,'application_database'):store=AgentStore.connect(os.environ['INSURANCE_POSTGRES_DSN'])
 try:
  yield SimpleNamespace(root=root,store=store,settings=settings,
   model=DeepSeekChat(ChatSettings.from_environment()) if load_model else None,
   release_factory=current_release,
   tools_factory=lambda release:SourceLibraryTools(store,root),
   workflow_store=WorkflowStore(store))
 finally:store.close()
