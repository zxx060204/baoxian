"""Backend-maintained material dates, separate from immutable rules and analysis."""
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.store import Forbidden


class MaterialDates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acquired_at: datetime | None = None
    version_date: date | None = None
    last_verified_at: datetime | None = None
    verification_note: str = ''


def metadata(app, owner, product_version_id):
    key = 'material-dates:' + stable_hash([owner, product_version_id])
    try:
        return IntakeStore(app.store).get('material_metadata', key, owner)['content']
    except Forbidden:
        return {'product_version_id': product_version_id, 'acquired_at': None,
                'version_date': None, 'last_verified_at': None, 'verification_note': ''}


def maintain(app, owner, product_version_id, values):
    values = MaterialDates.model_validate(values).model_dump(mode='json')
    values = {**values, 'product_version_id': product_version_id}
    store = IntakeStore(app.store)
    key = 'material-dates:' + stable_hash([owner, product_version_id])
    try:
        prior = store.get('material_metadata', key, owner)
    except Forbidden:
        return store.create('material_metadata', key, owner, product_version_id, values, 'maintained')['content']
    return store.update('material_metadata', key, prior['revision'], values, 'maintained', 'backend', 'maintain_material_dates')['content']
