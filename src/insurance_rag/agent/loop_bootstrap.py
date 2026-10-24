"""Explicit composition of the native loop, with no graph imports or checkpointer."""
import os
from pathlib import Path
from contextlib import contextmanager
from insurance_rag.agent.runtime_binding import BOOT_SNAPSHOT,RuntimeSnapshot
from insurance_rag.runtime_env import load_env
from insurance_rag.services.config import ChatSettings,AgentSettings
from insurance_rag.agent.models import DeepSeekChat
from insurance_rag.agent.model_adapter import NativeToolModel
from insurance_rag.agent.store import AgentStore
from insurance_rag.agent.engine import AgentEngine
from insurance_rag.services.source_library import SourceLibraryTools,current_release
@contextmanager
def open_loop_application(root=None,*,model=None,mode=None):
 root=Path(root or Path(__file__).resolve().parents[3]);load_env(root/'.env')
 snapshot=BOOT_SNAPSHOT if root.resolve()==BOOT_SNAPSHOT.root else RuntimeSnapshot(root)
 code=snapshot.binding(mode or os.environ.get('INSURANCE_AGENT_RETRIEVAL_MODE','rrf'))
 settings=AgentSettings.from_environment()
 from insurance_rag.reliability.diagnostics import stage
 with stage(root,'application_database'):store=AgentStore.connect(os.environ['INSURANCE_POSTGRES_DSN'])
 try:
  app=AgentEngine(store,settings,model or NativeToolModel(DeepSeekChat(ChatSettings.from_environment())),current_release,lambda release:SourceLibraryTools(store,root),root=root,code_binding=code)
  app.runtime_guard=snapshot.assert_current
  snapshot.assert_current()
  yield app
 finally:store.close()
