# @file: backend/nlp/evaluate.py
# @exports: evaluate, load_dataset, annotated_item
# @depends_on: backend/nlp/{gatekeeper_model,extractor}.py
# @purpose: Reproducible relevance/temporal PRF, timing, field accuracy, and lane-abstention metrics.
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from backend.core.config import ROOT, get_settings
from backend.nlp.extractor import IncidentExtractor
from backend.nlp.gatekeeper_model import GatekeeperModel
from backend.schemas.ingestion import RawIngestedItem


def load_dataset(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Dataset must contain unique annotated IDs')
    if any(row.get('source_name', '').startswith('fixture:') for row in rows):
        raise ValueError('Ingestion fixtures are not evaluation/training data')
    return rows


def annotated_item(row):
    return RawIngestedItem(report_id=uuid5(NAMESPACE_URL, row['id']), source_name='annotated_eval',
        source_url=f'https://annotations.trafficsense.invalid/{row["id"]}', title=row['text'],
        raw_text=row['text'], published_at='2026-10-01T00:00:00Z')


def prf(truth, predicted, positive=True):
    tp = sum(a == positive and b == positive for a, b in zip(truth, predicted))
    fp = sum(a != positive and b == positive for a, b in zip(truth, predicted))
    fn = sum(a == positive and b != positive for a, b in zip(truth, predicted))
    precision, recall = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    return {'precision': precision, 'recall': recall,
            'f1': 2*precision*recall/(precision+recall) if precision+recall else 0.0}


async def evaluate(path, classifier=None, extractor=None):
    rows = load_dataset(path)
    reference = get_settings().model_copy(update={'inference_mode': 'calibrated_teacher'})
    classifier, extractor = classifier or GatekeeperModel(reference), extractor or IncidentExtractor(reference)
    labels, predicted, relevance, predicted_relevance, times, extraction_times = [], [], [], [], [], []
    correct, fields, per_field, abstained_correct, abstained_predictions, absent_slots, absent_correct = 0, 0, {}, 0, 0, 0, 0
    versions = set()
    for row in rows:
        item = annotated_item(row)
        started = perf_counter()
        result = await classifier.classify(item)
        times.append((perf_counter()-started)*1000)
        versions.add(result.model_version)
        labels.append(row['temporal_status'])
        predicted.append(str(result.temporal_status))
        relevance.append(row['is_relevant'])
        predicted_relevance.append(result.is_relevant)
        started = perf_counter()
        attributes = (await extractor.extract(item)).model_dump(mode='json')
        extraction_times.append((perf_counter()-started)*1000)
        for name, expected in row['expected'].items():
            value = attributes
            for part in name.split('.'):
                value = value[part]
            match = value == expected
            correct += int(match)
            fields += 1
            totals = per_field.setdefault(name, [0, 0])
            totals[0] += int(match)
            totals[1] += 1
            if name in ('lanes_blocked', 'total_lanes'):
                absent_slots += int(expected is None)
                absent_correct += int(expected is None and value is None)
                abstained_predictions += int(value is None)
                abstained_correct += int(value is None and expected is None)
    classes = ['active', 'cleared', 'scheduled_future', 'unrelated_noise']
    temporal = {label: prf(labels, predicted, label) for label in classes}
    return {'dataset_count': len(rows), 'dataset_kind': 'authored_synthetic_annotations_not_field_validation',
        'model_versions': sorted(versions), 'relevance': prf(relevance, predicted_relevance),
        'temporal': temporal, 'temporal_macro': {key: sum(temporal[c][key] for c in classes)/4
                                               for key in ('precision', 'recall', 'f1')},
        'classification_latency_ms_per_item': sum(times)/len(times),
        'extraction_latency_ms_per_item': sum(extraction_times)/len(extraction_times),
        'field_accuracy': correct/fields if fields else 0,
        'per_field_accuracy': {key: good/total for key, (good, total) in per_field.items()},
        'abstention_precision': abstained_correct/abstained_predictions if abstained_predictions else None,
        'absent_lane_abstention_recall': absent_correct/absent_slots if absent_slots else None,
        'correct_fields': correct, 'evaluated_fields': fields}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT/'training/data/indian_traffic_eval.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT/'training/reports/teacher_eval.json')
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.data))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
