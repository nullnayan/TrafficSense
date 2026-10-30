# @file: tests/test_nlp.py
# @exports: Relevance/temporal, lane abstention, local loading, cloud-contract, and evaluation tests
# @depends_on: backend/nlp
# @purpose: Exercise NLP semantics with deterministic inputs and zero external model/API calls.
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from backend.core.config import ROOT, get_settings
from backend.nlp.evaluate import evaluate
from backend.nlp.extractor import IncidentExtractor, reference_extract
from backend.nlp.gatekeeper_model import GatekeeperModel, teacher_result
from backend.nlp.linguistics import lane_evidence
from backend.schemas.ingestion import RawIngestedItem


def item(text='Landslide blocks NH-7 near Kali Mandir in Dehradun.'):
    return RawIngestedItem(source_name='annotated_test', source_url='https://example.org/nlp',
                           title=text, raw_text=text, published_at='2026-10-01T00:00:00Z')


@pytest.mark.parametrize('text,relevant,status', [
    ('Landslide blocks NH-7.', True, 'active'),
    ('Fatal car crash near Dehradun.', True, 'active'),
    ('Traffic restored on NH-7 after landslide.', True, 'cleared'),
    ('Highway blockade lifted near Dehradun.', True, 'cleared'),
    ('Bridge opened for vehicular movement.', True, 'cleared'),
    ('NH-44 will remain closed tomorrow.', True, 'scheduled_future'),
    ('Cricket fever brings city to a standstill.', False, 'unrelated_noise'),
    ('Stock market crash causes traffic standstill.', False, 'unrelated_noise'),
    ('NH-7 has not reopened after a landslide.', True, 'active'),
    ('Traffic restored briefly but NH-7 remains blocked.', True, 'active'),
])
def test_relevance_then_temporal(text, relevant, status):
    result = teacher_result(item(text))
    assert result.is_relevant == relevant and result.temporal_status == status
    assert 0 <= result.confidence <= 1 and result.model_version


@pytest.mark.parametrize('text,expected', [
    ('NH-7 blocked; several lanes closed.', (None, None)),
    ('All lanes blocked on NH-7.', (None, None)),
    ('Two of four lanes blocked on NH-7.', (2, 4)),
    ('Collision blocks one lane on a three-lane road.', (1, 3)),
    ('At least two lanes blocked.', (None, None)),
    ('Zero of two lanes blocked.', (0, 2)),
    ('Three of two lanes blocked.', (None, None)),
])
def test_explicit_lane_evidence(text, expected):
    assert lane_evidence(text) == expected


def test_reference_landmark_and_abstention():
    result = reference_extract(item())
    assert result.landmark_tuple.corridor_name == 'NH-7'
    assert result.landmark_tuple.primary_landmark == 'Kali Mandir'
    assert result.landmark_tuple.city == 'Dehradun'
    assert result.lanes_blocked is result.total_lanes is None


@pytest.mark.asyncio
async def test_local_model_is_lazy_cached_and_threaded(tmp_path, monkeypatch):
    calls, thread_ids = [], []
    class FakeSetFit:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            calls.append(args)
            return cls()
        def predict(self, texts):
            thread_ids.append(threading.get_ident())
            return ['active']
        def predict_proba(self, texts):
            return SimpleNamespace(tolist=lambda: [[0.95, 0.02, 0.02, 0.01]])
    monkeypatch.setitem(__import__('sys').modules, 'setfit', SimpleNamespace(SetFitModel=FakeSetFit))
    settings = get_settings().model_copy(update={'inference_mode': 'local_setfit', 'setfit_model_path': tmp_path})
    classifier = GatekeeperModel(settings)
    assert calls == []
    assert (await classifier.classify(item())).confidence == 0.95
    await classifier.classify(item())
    assert len(calls) == 1 and all(thread != threading.get_ident() for thread in thread_ids)


@pytest.mark.asyncio
async def test_cloud_schema_and_hallucinated_lane_guard():
    sample = item()
    output = reference_extract(sample).model_dump(mode='json')
    output.update(lanes_blocked=2, total_lanes=4)
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})
    settings = get_settings().model_copy(update={'inference_mode': 'zero_shot_api',
        'llm_api_key': __import__('pydantic').SecretStr('test-only-key'), 'llm_model_id': 'test-model'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await IncidentExtractor(settings, client).extract(sample)
    assert result.lanes_blocked is result.total_lanes is None
    assert requests[0]['temperature'] == 0.0
    schema = requests[0]['response_format']['json_schema']
    assert schema['strict'] and schema['schema']['additionalProperties'] is False
    assert set(schema['schema']['required']) == set(schema['schema']['properties'])


@pytest.mark.asyncio
async def test_cloud_rejects_changed_identity():
    sample = item()
    output = reference_extract(sample).model_dump(mode='json')
    output['report_id'] = str(uuid4())
    settings = get_settings().model_copy(update={'inference_mode': 'cloud_finetuned_api',
        'llm_api_key': __import__('pydantic').SecretStr('test-only-key'), 'finetuned_llm_model_id': 'fine-tuned-test'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'choices': [{'message': {'content': json.dumps(output)}}]}))) as client:
        with pytest.raises(ValueError, match='identity'):
            await IncidentExtractor(settings, client).extract(sample)


@pytest.mark.asyncio
async def test_evaluation_metrics_run_on_24_annotations():
    report = await evaluate(ROOT/'training/data/indian_traffic_eval.jsonl')
    assert report['dataset_count'] == 24 and report['evaluated_fields'] == 240
    assert report['abstention_precision'] == report['absent_lane_abstention_recall'] == 1.0
    assert report['field_accuracy'] > 0.9 and report['classification_latency_ms_per_item'] >= 0
    assert set(report['relevance']) == {'precision', 'recall', 'f1'}
