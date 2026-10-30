# @file: backend/spatial/__init__.py
# @exports: Geocoder, import_gazetteer, cluster_report, SpatialService, process_extracted_stream_batch
# @depends_on: backend/spatial/{geocoder,gazetteer_loader,clustering,worker,pipeline}.py
# @purpose: Lazy spatial package exports without CLI import side effects.
from importlib import import_module

EXPORTS = {'Geocoder': 'geocoder', 'import_gazetteer': 'gazetteer_loader',
           'cluster_report': 'clustering', 'SpatialService': 'worker',
           'process_extracted_stream_batch': 'pipeline'}
__all__ = list(EXPORTS)


def __getattr__(name):
    if name not in EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(f'backend.spatial.{EXPORTS[name]}'), name)
