# @file: tests/test_nlp_artifacts.py
# @exports: Training/data/notebook integrity and NLP API telemetry tests
# @depends_on: training, backend/main.py, backend/nlp
# @purpose: Validate shipped training artifacts without installing models or executing GPU cells.
import ast
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from backend.core.config import ROOT
from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.main import app
from backend.nlp.evaluate import load_dataset
from backend.nlp.pipeline import process_raw_stream_batch
from backend.nlp.worker import NLPBusy
from backend.schemas.assistant import SlotFillingQuery
from backend.schemas.extraction import ExtractedIncidentSchema
from training.train_setfit import train


def test_disjoint_training_and_valid_targets():
    training = load_dataset(ROOT/'training/data/indian_traffic_train.jsonl')
    evaluation = load_dataset(ROOT/'training/data/indian_traffic_eval.jsonl')
    assert len(training) == 80 and len(evaluation) == 24
    assert Counter((row['is_relevant'], row['temporal_status']) for row in training) == {
        (True, 'active'): 20, (True, 'cleared'): 20,
        (True, 'scheduled_future'): 20, (False, 'unrelated_noise'): 20}
    assert len({row['text'] for row in training}) == 80
    assert len({row['id'] for row in training}) == 80
    assert not {row['text'] for row in training} & {row['text'] for row in evaluation}
    for row in training + evaluation:
        target = {'report_id': '10000000-0000-4000-8000-000000000001', 'landmark_tuple': {}}
        for name, value in row['expected'].items():
            if name.startswith('landmark_tuple.'):
                target['landmark_tuple'][name.split('.')[1]] = value
            else:
                target[name] = value
        ExtractedIncidentSchema.model_validate(target)
    for row in load_dataset(ROOT/'training/data/assistant_train.jsonl'):
        SlotFillingQuery.model_validate(row['output'])


def test_clean_colab_notebook():
    notebook = json.loads((ROOT/'training/TrafficSense_QLoRA_Finetune.ipynb').read_text(encoding='utf-8'))
    assert notebook['nbformat'] == 4 and notebook['metadata']['accelerator'] == 'GPU'
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            assert cell['execution_count'] is None and cell['outputs'] == []
            ast.parse(''.join(cell['source']))


def test_training_dry_run_reports_no_trained_weights(tmp_path):
    args = SimpleNamespace(train=ROOT/'training/data/indian_traffic_train.jsonl',
        eval=ROOT/'training/data/indian_traffic_eval.jsonl', seed=42, encoder='test-encoder',
        output=tmp_path/'model', report=tmp_path/'benchmark.json', dry_run=True)
    train(args)
    report = json.loads(args.report.read_text())
    assert report['setfit'] is None and report['status'] == 'dry_run_no_training'
    assert not args.output.exists()


def test_shipped_setfit_report_status_and_metrics():
    report = json.loads((ROOT/'training/reports/setfit_benchmark.json').read_text(encoding='utf-8'))
    assert report['training_count'] == 80
    assert report['status'] in {'dry_run_no_training', 'trained_and_evaluated'}
    if report['status'] == 'dry_run_no_training':
        assert report['setfit'] is None
    else:
        metrics = report['setfit']
        assert metrics['dataset_count'] == 24 and metrics['model_versions']
        for key in ('relevance', 'temporal_macro'):
            assert set(metrics[key]) == {'precision', 'recall', 'f1'}
            assert all(0 <= value <= 1 for value in metrics[key].values())
        assert set(metrics['temporal']) == {'active', 'cleared', 'scheduled_future', 'unrelated_noise'}
        for values in metrics['temporal'].values():
            assert set(values) == {'precision', 'recall', 'f1'}
            assert all(0 <= value <= 1 for value in values.values())
        assert metrics['classification_latency_ms_per_item'] >= 0


def test_qlora_training_provenance_and_warm_latency():
    report = json.loads((ROOT/'training/reports/qlora_training_metrics.json').read_text(encoding='utf-8'))
    assert report['train_runtime'] == pytest.approx(136.3448)
    assert report['train_loss'] == pytest.approx(0.9643258611)
    assert report['provenance']['field_validated'] is False
    assert report['provenance']['inference_accuracy'] == 'not_measured'
    setfit = json.loads((ROOT/'training/reports/setfit_benchmark.json').read_text(encoding='utf-8'))
    assert setfit['setfit']['classification_latency_ms_per_item'] == pytest.approx(455.0515)
    if 'warm_inference' in setfit:
        warm = setfit['warm_inference']
        assert warm['model_load_ms'] >= 0 and warm['warm_mean_ms_per_item'] > 0
        assert warm['sample_count'] >= warm['dataset_count']
        assert warm['field_validated'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('busy', [False, True])
async def test_nlp_api_telemetry_and_lock(busy):
    service = SimpleNamespace(process=AsyncMock(return_value={'processed_raw': 4, 'active_forwarded': 1,
        'dropped_noise_or_inactive': 3, 'extracted_count': 1, 'dead_letter_count': 0}))
    if busy:
        service.process.side_effect = NLPBusy('Busy')
    with patch.object(app.state, 'nlp', service, create=True):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/v1/nlp/process')
    assert response.status_code == (409 if busy else 200)
    if not busy:
        assert response.json()['extracted_count'] == 1


@pytest.mark.asyncio
async def test_poison_routing_works_without_database_or_api_keys():
    async with FakeRedis(decode_responses=True) as client:
        bus = RedisStreamBus(client)
        await client.xadd(StreamNames.RAW_ITEMS, {'payload': '{broken'})
        session = AsyncMock()
        result = await process_raw_stream_batch(session, bus)
        assert result['dead_letter_count'] == 1
        assert (await client.xpending(StreamNames.RAW_ITEMS, 'nlp_filter_group'))['pending'] == 0
        session.execute.assert_not_called()
