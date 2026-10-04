from evaluation.model_quality import run
from evaluation.study_summary import summarize
from mandate.agent.selector import Pick, Selection, SelectorError


def test_absent_model_key_is_not_configured(monkeypatch):
    def unavailable():
        raise SelectorError('not configured')
    monkeypatch.setattr('evaluation.model_quality.typesafe_key', unavailable)
    report = run()
    assert report['status'] == 'not_configured' and not report['cases']


def test_model_boundary_checks_never_allow_blocked_or_unknown():
    class Selector:
        def choose(self, items, products, instruction):
            return Selection([Pick(items[0]['name'], items[0]['quantity'], products[0]['id'], 1, '')], 'fixture-not-live')
    report = run(Selector())
    assert report['status'] == 'completed' and len(report['cases']) == 10
    assert report['unauthorized_allowed'] == 0


def test_study_pairs_retain_failure_and_do_not_claim_price_parity():
    shared = {'participant': 'P01', 'task_id': 'fixed', 'finished_at': '2026-10-04T00:00:00Z', 'outcome': 'ready', 'actions': 5, 'execution_mode': 'human'}
    runs = [{**shared, 'mode': 'manual', 'elapsed_seconds': 50}, {**shared, 'mode': 'agent', 'elapsed_seconds': 40, 'execution_mode': 'model'}, {**shared, 'participant': 'P02', 'mode': 'agent', 'outcome': 'failed', 'elapsed_seconds': 60}]
    out = summarize(runs)
    assert out['failed_or_abandoned'] == 1
    assert out['pairs'][0]['difference_seconds'] == 10
    assert out['pairs'][0]['price_parity_asserted'] is False
    assert not summarize(runs + [runs[0]])['pairs']
