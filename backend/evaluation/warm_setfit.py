# @file: backend/evaluation/warm_setfit.py
# @exports: benchmark, main
# @depends_on: backend/nlp/gatekeeper_model.py, backend/nlp/evaluate.py, setfit
# @purpose: Separate local model load and warm classification timings, preserving historical latency.
import asyncio
import json
import os
import statistics
from time import perf_counter

from backend.core.config import ROOT, get_settings
from backend.nlp.evaluate import annotated_item, load_dataset
from backend.nlp.gatekeeper_model import GatekeeperModel
from backend.schemas.ingestion import utc_now


async def benchmark(repeats=2):
    # All weights/tokenizers must already be local; prevent implicit remote downloads.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from setfit import SetFitModel
    torch.set_num_threads(2)
    settings = get_settings().model_copy(update={'inference_mode': 'local_setfit'})
    gate = GatekeeperModel(settings)
    items = [annotated_item(row) for row in load_dataset(ROOT / 'training/data/indian_traffic_eval.jsonl')]
    start = perf_counter()
    gate._model = await asyncio.to_thread(SetFitModel.from_pretrained, str(settings.setfit_model_path),
                                         local_files_only=True, device='cpu')
    load_ms = (perf_counter() - start) * 1000
    start = perf_counter()
    first = await gate.classify(items[0])
    first_ms = (perf_counter() - start) * 1000
    if not first.model_version.startswith('local_setfit:'):
        raise RuntimeError('Local SetFit was not used; refusing to label fallback as warm inference')
    timings = []
    for _ in range(repeats):
        for item in items:
            start = perf_counter()
            result = await gate.classify(item)
            timings.append((perf_counter() - start) * 1000)
            if not result.model_version.startswith('local_setfit:'):
                raise RuntimeError('Local inference fell back during timing')
    report = {'measured_at': utc_now().isoformat(), 'provenance': 'local_authored_synthetic_warm_inference',
              'model_load_ms': round(load_ms, 3), 'first_inference_ms': round(first_ms, 3),
              'warm_mean_ms_per_item': round(statistics.mean(timings), 3),
              'warm_median_ms_per_item': round(statistics.median(timings), 3),
              'warm_p90_ms_per_item': round(sorted(timings)[int(.9 * (len(timings) - 1))], 3),
              'sample_count': len(timings), 'dataset_count': len(items), 'repeats': repeats,
              'torch_threads': 2, 'device': str(gate._model.model_body.device), 'field_validated': False,
              'limitations': 'Warm CPU measurement excludes model load; host load and thread settings affect latency.'}
    path = ROOT / 'training/reports/setfit_benchmark.json'
    existing = json.loads(path.read_text(encoding='utf-8'))
    existing['warm_inference'] = report
    existing['historical_latency'] = {'classification_latency_ms_per_item':
        existing['setfit']['classification_latency_ms_per_item'],
        'provenance': 'previous_recorded_cold_or_throttled_evaluation; load isolation not recorded'}
    path.write_text(json.dumps(existing, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    print(json.dumps(asyncio.run(benchmark()), indent=2))


if __name__ == '__main__':
    main()
