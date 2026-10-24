"""Separate offline extraction output budget from interactive chat settings."""
import os
from dataclasses import replace

def extraction_model(model):
 from insurance_rag.agent.models import DeepSeekChat
 if not isinstance(model,DeepSeekChat):return model
 try:tokens=int(os.environ.get('INSURANCE_FORMULA_MAX_OUTPUT_TOKENS','6144'))
 except ValueError:raise ValueError('invalid_formula_output_budget') from None
 if not 1024<=tokens<=8192:raise ValueError('invalid_formula_output_budget')
 extracted=DeepSeekChat(replace(model.settings,max_output_tokens=tokens))
 # Offline extraction is the same charged provider session with a larger budget.
 extracted.usage=model.usage
 return extracted
