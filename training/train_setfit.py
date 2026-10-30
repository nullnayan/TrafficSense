# @file: training/train_setfit.py
# @exports: main, train
# @depends_on: backend/nlp/evaluate.py, training/requirements-setfit.txt
# @purpose: Disjoint-data local SetFit training and honest teacher-versus-SetFit benchmarking.
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import get_settings  # noqa: E402
from backend.nlp.evaluate import evaluate, load_dataset  # noqa: E402
from backend.nlp.gatekeeper_model import GatekeeperModel  # noqa: E402

LABELS = ['active', 'cleared', 'scheduled_future', 'unrelated_noise']


def train(args):
    train_rows, eval_rows = load_dataset(args.train), load_dataset(args.eval)
    if {r['text'].casefold().strip() for r in train_rows} & {r['text'].casefold().strip() for r in eval_rows}:
        raise ValueError('Train/evaluation text overlap is forbidden')
    if {r['id'] for r in train_rows} & {r['id'] for r in eval_rows}:
        raise ValueError('Train/evaluation IDs overlap')
    if set(r['temporal_status'] for r in train_rows) != set(LABELS):
        raise ValueError('Training data must include all four temporal/relevance classes')
    report = {'seed': args.seed, 'encoder': args.encoder, 'training_count': len(train_rows),
              'evaluation_count': len(eval_rows), 'teacher': asyncio.run(evaluate(args.eval)),
              'setfit': None, 'status': 'dry_run_no_training',
              'limitations': 'Small authored synthetic corpus; not an independent real-world validation.'}
    if not args.dry_run:
        from datasets import Dataset
        from setfit import SetFitModel, Trainer, TrainingArguments
        dataset = Dataset.from_dict({'text': [r['text'] for r in train_rows],
                                     'label': [LABELS.index(r['temporal_status']) for r in train_rows]})
        model = SetFitModel.from_pretrained(args.encoder, labels=LABELS)
        trainer = Trainer(model=model, train_dataset=dataset, args=TrainingArguments(
            seed=args.seed, num_epochs=1, batch_size=8, num_iterations=10,
        ))
        trainer.train()
        args.output.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(args.output))
        settings = get_settings().model_copy(update={'inference_mode': 'local_setfit',
                                                      'setfit_model_path': args.output.resolve()})
        report['setfit'] = asyncio.run(evaluate(args.eval, classifier=GatekeeperModel(settings)))
        if any('fallback' in version for version in report['setfit']['model_versions']):
            raise RuntimeError('SetFit benchmark fell back to teacher; refusing mislabeled comparison')
        report['status'] = 'trained_and_evaluated'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'report': str(args.report)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--train', type=Path, default=ROOT/'training/data/indian_traffic_train.jsonl')
    parser.add_argument('--eval', type=Path, default=ROOT/'training/data/indian_traffic_eval.jsonl')
    parser.add_argument('--encoder', default='sentence-transformers/paraphrase-multilingual-mpnet-base-v2')
    parser.add_argument('--output', type=Path, default=ROOT/'models/setfit_gatekeeper')
    parser.add_argument('--report', type=Path, default=ROOT/'training/reports/setfit_benchmark.json')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--dry-run', action='store_true')
    train(parser.parse_args())


if __name__ == '__main__':
    main()
