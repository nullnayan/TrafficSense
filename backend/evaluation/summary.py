# @file: backend/evaluation/summary.py
# @exports: summarize, main
# @depends_on: training/reports/*.json, benchmarks/*.json
# @purpose: Aggregate measured evidence without treating training loss or fixtures as field validation.
import argparse
import hashlib
import json
from pathlib import Path

from backend.core.config import ROOT
from backend.schemas.ingestion import utc_now

SOURCES = {
    'teacher': ('training/reports/teacher_eval.json', 'authored_synthetic_annotations'),
    'setfit': ('training/reports/setfit_benchmark.json', 'authored_synthetic_annotations'),
    'qlora': ('training/reports/qlora_training_metrics.json', 'user_supplied_colab_training_log'),
    'spatial': ('benchmarks/spatial_lookup.json', 'small_local_gazetteer_sql_benchmark'),
    'clearance': ('benchmarks/clearance_waterfall.json', 'authored_synthetic_clearance_references'),
    'scout_assistant': ('benchmarks/scout_assistant_verification.json', 'local_fixture_api_verification'),
    'pipeline': ('benchmarks/pipeline_demo.json', 'synthetic_fixture_pipeline'),
    'pipeline_replay': ('benchmarks/pipeline_demo_replay.json', 'synthetic_fixture_pipeline_replay'),
    'verification': ('benchmarks/phase7_verification.json', 'local_repository_verification'),
}


def summarize(root: Path = ROOT, output: Path | None = None):
    evidence = {}
    for name, (relative, provenance) in SOURCES.items():
        path = root / relative
        entry = {'source': relative, 'provenance': provenance, 'field_validated': False,
                 'status': 'missing_not_measured', 'metrics': None}
        if path.is_file():
            raw = path.read_bytes()
            entry.update(status='recorded', sha256=hashlib.sha256(raw).hexdigest(),
                         metrics=json.loads(raw.decode('utf-8-sig')))
        evidence[name] = entry
    adapters = root / 'models/qwen25_3b_lora'
    artifact_status = {'directory': 'models/qwen25_3b_lora',
                       'adapter_config_present': (adapters / 'adapter_config.json').is_file(),
                       'adapter_weights_present': (adapters / 'adapter_model.safetensors').is_file(),
                       'inference_accuracy': 'not_measured', 'runtime_loading': 'not_verified'}
    result = {'generated_at': utc_now().isoformat(), 'project': 'TrafficSense', 'phase': 7,
              'field_validation_status': 'not_performed', 'evidence': evidence,
              'qlora_local_artifacts': artifact_status,
              'limitations': ['Authored fixtures are developmental evidence, not independent field validation.',
                              'Training loss is not an extraction accuracy measurement.',
                              'Small warm SQL timings do not establish national-scale performance.',
                              'Predictive p90 bounds remain uncalibrated against field clearance times.',
                              'Missing evidence stays missing; no invented measurements.']}
    destination = output or root / 'benchmarks/thesis_evaluation_summary.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description='Aggregate recorded thesis benchmark evidence')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = summarize(output=args.output)
    print(json.dumps({'output': str(args.output or ROOT / 'benchmarks/thesis_evaluation_summary.json'),
                      'recorded_sources': [k for k, v in result['evidence'].items() if v['status'] == 'recorded']},
                     indent=2))


if __name__ == '__main__':
    main()
