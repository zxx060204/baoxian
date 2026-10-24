"""Agent-facing capability view over existing public catalog and private rules."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from insurance_rag.tools.contracts import result


class ProductScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    product_version_id: str | None = Field(default=None, max_length=200)
    bundle_id: str | None = Field(default=None, max_length=200)
    conditions: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.product_version_id) == bool(self.bundle_id):
            raise ValueError('one_product_source_required')
        return self


def item(name, status, *, required=(), missing=(), next_step=None, raw=None):
    return dict(capability=name, status=status, required_inputs=list(required),
                missing_inputs=list(missing), next_step=next_step, details=raw or {})


def capability_view(args, c):
    """Read readiness without preparing data, calculating or changing user memory."""
    conditions = {**c['state'].get('conditions', {}), **args.get('conditions', {})}
    if args.get('bundle_id'):
        from insurance_rag.ingestion.store import bundle
        current=bundle(c['app'],c['owner'],args['bundle_id'])['content']
        if current.get('scope')=='current_source_business':
            from insurance_rag.ingestion.readiness import current_capabilities
            raw=current_capabilities(c['app'],current)
            actions={'document_qa':{'tool':'product_documents','action':'analyze'},
                     'source_tables':{'tool':'query_product_source_values','action':'tables'},
                     'rule_extraction':{'tool':'get_product_table_rules'},
                     'source_examples':{'tool':'read_product_example'},
                     'formula_execution':{'tool':'get_product_table_rules'},'illustration':{'tool':'get_product_table_rules'}}
            entries=[]
            for name,entry in raw.items():
                status=('ready' if entry['status'] in ('available','prepared','imported','partial','ready')
                        else 'not_applicable' if entry['status']=='not_applicable' else 'needs_preparation')
                if name=='source_tables' and not entry.get('table_count'):status='needs_preparation'
                if entry.get('reason_code')=='current_source_execution_not_prepared':
                    status='needs_implementation'
                    next_step={'by':'backend','action':'implement_personal_execution'}
                elif status=='ready':next_step=actions.get(name)
                else:next_step={'by':'user_or_backend','action':'prepare_required_source_module','module':name} if status!='not_applicable' else None
                if entry['status']=='partial' or name=='rule_extraction' and entry.get('failed_count'):status='partial'
                if entry['status']=='blocked':status='needs_materials'
                value=item('personal_illustration' if name=='illustration' else name,status,next_step=next_step,raw=entry)
                for key in ('reason_code','scope','execution_readiness','target_count','rule_count','failed_count'):
                    if key in entry:value[key]=entry[key]
                entries.append(value)
                if name=='illustration':
                    for target in entry.get('targets',[]):
                        required=target['required_inputs'];missing=[k for k in required if conditions.get(k) is None]
                        target_status='needs_inputs' if target['state']=='ready' and missing else target['state']
                        entries.append(item(target['id'],target_status,required=required,missing=missing,next_step={'tool':'get_product_table_rules'},raw=target))
            from insurance_rag.services.material_metadata import metadata
            return result('ok',data={'capability_view':{'product':current['identity'],
                'source_scope':current['scope'],'capabilities':entries,'conditions_are_preview_only':True,
                'material_times':metadata(c['app'],bundle(c['app'],c['owner'],args['bundle_id'])['owner_id'],current['identity']['product_version_id']),
                'policy':'per_target_execution_readiness'}})
        raise ValueError('retired_bundle_requires_migration')
    from insurance_rag.ingestion.library import discover
    matches=[p for p in discover(c) if p['product_version_id']==args['product_version_id']]
    if len(matches)==1:return capability_view({'bundle_id':matches[0]['bundle_id'],'conditions':args.get('conditions',{})},c)
    if len(matches)>1:return result('needs_clarification',reason_code='product_bundle_selection_required',missing_fields=['bundle_id'],data={'products':matches})
    from insurance_rag.agent.store import Forbidden
    raise Forbidden('product_unavailable')
