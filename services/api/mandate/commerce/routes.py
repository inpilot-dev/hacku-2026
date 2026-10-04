from typing import Annotated, Literal
import json
from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field, StrictInt, StrictStr
from mandate.payments.auth import Actor, current_actor, require_role
from mandate.payments.models import Strict, Policy, QuoteItemRequest
from mandate.payments.errors import invalid, conflict
from mandate.payments.clock import iso
from .planning import preview, optimize
from .study import Studies
from .recovery import Recovery, digest
from .credentials import Credentials
from .groups import Groups


def user(actor: Actor = Depends(current_actor)):
    return require_role(actor, 'user')

User = Annotated[Actor, Depends(user)]
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)]
Text = Annotated[StrictStr, Field(min_length=1, max_length=128)]
NonNegative = Annotated[StrictInt, Field(ge=0, le=1000000)]


class PreviewInput(Strict):
    policy: Policy
    parent_mandate_id: Text | None = None


class PlanInput(Strict):
    mandate_id: Text
    items: list[QuoteItemRequest] = Field(min_length=1, max_length=12)


class StudyStart(Strict):
    participant: Annotated[StrictStr, Field(pattern=r'^P[0-9]{2,4}$')]
    task_id: Text
    mode: Literal['manual', 'agent']
    quote_id: Text
    setup_seconds: NonNegative
    execution_mode: Literal['human', 'scripted', 'model', 'fallback']


class StudyFinish(Strict):
    actions: NonNegative
    errors: NonNegative
    outcome: Literal['ready', 'failed', 'abandoned']
    source_url: Annotated[StrictStr, Field(max_length=1000)]
    observed_at: Annotated[StrictStr, Field(max_length=64)]
    note: Annotated[StrictStr, Field(max_length=1000)] = ''


class SandboxStart(Strict):
    mandate_id: Text
    quote_id: Text
    approved_quote_hash: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')]
    scenario: Literal['happy', 'order_failure', 'lost_capture_response', 'pending_refund', 'failed_refund'] = 'happy'


class GroupStart(Strict):
    purchases: list[SandboxStart] = Field(min_length=2, max_length=3)


class CredentialTemplate(Strict):
    mandate_id: Text
    public_x: Annotated[StrictStr, Field(min_length=43, max_length=43)]


class CredentialVerify(Strict):
    credential: Annotated[StrictStr, Field(min_length=1, max_length=30000)]


def build_commerce_router(wallet, recovery=None):
    router = APIRouter(tags=['commerce'])
    studies = Studies(wallet)
    recovery = recovery or Recovery(wallet)
    credentials = Credentials(wallet)
    groups = Groups(wallet, recovery)

    @router.post('/commerce/preview')
    def policy_preview(actor: User, body: PreviewInput):
        return preview(wallet, actor, body.policy.model_dump(mode='json'), body.parent_mandate_id)

    @router.post('/commerce/plans')
    def basket_plans(actor: User, body: PlanInput):
        return optimize(wallet, actor, body.mandate_id, [i.model_dump() for i in body.items])

    @router.get('/commerce/studies')
    def list_studies(actor: User):
        return {'runs': studies.list(actor)}

    @router.post('/commerce/studies', status_code=201)
    def start_study(actor: User, body: StudyStart):
        return studies.start(actor, body.model_dump())

    @router.post('/commerce/studies/{run_id}/finish')
    def finish_study(actor: User, run_id: str, body: StudyFinish):
        from mandate.payments.clock import parse
        try:
            parse(body.observed_at)
        except ValueError:
            raise invalid('Observation time must include a timezone.') from None
        if not body.source_url.startswith(('https://', 'http://127.0.0.1:', 'snapshot:')):
            raise invalid('Record the retailer URL or snapshot reference.')
        return studies.finish(actor, run_id, body.model_dump())

    @router.get('/commerce/provider')
    def provider_status(actor: User):
        return {'provider': 'local_sandbox', 'configured': True, 'live_payments': False, 'simulated': True}

    @router.get('/commerce/quotes/{quote_id}/approval')
    def exact_approval(actor: User, quote_id: str):
        q = wallet.get_quote(actor, quote_id)
        return {'quote': q, 'quote_hash': digest(q)}

    @router.get('/commerce/operations')
    def operations(actor: User):
        return {'operations': [j for j in recovery.list(actor) if not j.get('group_id')]}

    @router.post('/commerce/operations', status_code=201)
    def start_operation(actor: User, key: Key, body: SandboxStart):
        return recovery.create(actor, key, body.model_dump())

    @router.get('/commerce/operations/{operation_id}')
    def operation(actor: User, operation_id: str):
        return recovery.get(actor, operation_id)

    @router.post('/commerce/operations/{operation_id}/advance')
    def advance_operation(actor: User, operation_id: str):
        if recovery.get(actor, operation_id).get('group_id'):
            raise conflict('Retrieve the basket group to preserve partial-failure recovery.')
        return recovery.advance(actor, operation_id)

    @router.post('/commerce/operations/{operation_id}/cancel')
    def cancel_operation(actor: User, operation_id: str):
        if recovery.get(actor, operation_id).get('group_id'):
            raise conflict('Cancel the basket group so every store is recovered together.')
        return recovery.cancellation(actor, operation_id)

    @router.get('/commerce/groups')
    def list_groups(actor: User):
        return {'groups': groups.list(actor)}

    @router.post('/commerce/groups', status_code=201)
    def start_group(actor: User, key: Key, body: GroupStart):
        return groups.create(actor, key, body.model_dump())

    @router.post('/commerce/groups/{group_id}/advance')
    def advance_group(actor: User, group_id: str):
        return groups.advance(actor, group_id)

    @router.post('/commerce/groups/{group_id}/cancel')
    def cancel_group(actor: User, group_id: str):
        return groups.cancel(actor, group_id)

    @router.post('/commerce/credentials/template')
    def credential_template(actor: User, body: CredentialTemplate):
        return credentials.template(actor, body.mandate_id, body.public_x)

    @router.post('/commerce/credentials/verify')
    def verify_credential(actor: User, body: CredentialVerify):
        return credentials.verify(actor, body.credential)

    return router
