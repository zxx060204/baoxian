"""Explicit publication of backend product bundles; private uploads stay private."""
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.agent.store import Forbidden, Conflict
from insurance_rag.parsing.common import stable_hash


def key(bundle_id):
    return 'library-product:' + stable_hash(bundle_id)


def publication(app, bundle_id):
    row=IntakeStore(app.store).get('library_product',key(bundle_id))
    if row['state']!='published' or row['content']['bundle_id']!=bundle_id:
        raise Forbidden('library_product_unavailable')
    return row


def publish(app, owner, bundle_id, enabled=True):
    from insurance_rag.ingestion.store import bundle
    store=IntakeStore(app.store)
    parent=bundle(app,owner,bundle_id)
    if parent['owner_id']!=owner:
        raise Forbidden('only_owner_can_publish_product')
    content={'bundle_id':bundle_id,'source_owner':owner,'bundle_content_hash':parent['content_hash'],
             'identity':parent['content']['identity'],'published_at':app.store.clock()}
    state='published' if enabled else 'unpublished'
    try:
        old=store.get('library_product',key(bundle_id),owner)
    except Forbidden:
        return store.create('library_product',key(bundle_id),owner,parent['product_version_id'],content,state)
    return store.update('library_product',old['id'],old['revision'],content,state,'backend','library_publication_changed')


def candidates(app, hint=None):
    rows=[]
    for row in IntakeStore(app.store).list('library_product',limit=500):
        if row['state']!='published':
            continue
        i=row['content']['identity']
        if hint and hint not in i['product_name']:
            continue
        rows.append({'bundle_id':row['content']['bundle_id'],'product_version_id':i['product_version_id'],
                     'product_name':i['product_name'],'company_id':i.get('company_id'),
                     'catalog_source':'maintained_library','publication_status':'source_available',
                     'published_at':row['content']['published_at']})
    return rows


def discover(c,hint=None):
    import re
    compact=lambda value:re.sub(r'[\s（）()]','',value)
    rows=candidates(c['app']);seen={p['bundle_id'] for p in rows}
    for row in IntakeStore(c['app'].store).list('product_bundle',owner=c['owner'],limit=500):
        if row['id'] in seen or row['content'].get('scope')!='current_source_business':continue
        identity=row['content']['identity'];rows.append({'bundle_id':row['id'],'product_version_id':identity['product_version_id'],'product_name':identity['product_name'],'company_id':identity.get('company_id'),'catalog_source':'private_source_bundle','publication_status':'private'})
    return [p for p in rows if not hint or compact(hint) in compact(p['product_name'])]
