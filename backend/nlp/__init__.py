# @file: backend/nlp/__init__.py
# @exports: GatekeeperModel, IncidentExtractor, NLPService, process_raw_stream_batch, process_filtered_stream_batch, evaluate
# @depends_on: backend/nlp/{gatekeeper_model,extractor,worker,pipeline,evaluate}.py
# @purpose: Lazy public NLP exports without model imports/downloads or CLI import side effects.
from importlib import import_module

EXPORTS = {'GatekeeperModel': 'gatekeeper_model', 'IncidentExtractor': 'extractor',
           'NLPService': 'worker', 'process_raw_stream_batch': 'pipeline',
           'process_filtered_stream_batch': 'pipeline', 'evaluate': 'evaluate'}
__all__ = list(EXPORTS)


def __getattr__(name):
    if name not in EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(f'backend.nlp.{EXPORTS[name]}'), name)
