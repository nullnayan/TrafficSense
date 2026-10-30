# @file: backend/nlp/gatekeeper_model.py
# @exports: GatekeeperModel, teacher_result, get_gatekeeper
# @depends_on: backend/core/config.py, backend/schemas/extraction.py
# @purpose: Relevance then temporal classification with optional lazy threaded local SetFit.
import asyncio
import re
import threading
from functools import lru_cache

from backend.core.config import get_settings
from backend.nlp.linguistics import text_of
from backend.schemas.enums import TemporalStatus
from backend.schemas.extraction import SetFitGatekeeperResult

ROAD = re.compile(r'\b(?:nh\s*[-–]?\s*\d+|sh\s*[-–]?\s*\d+|road|highway|bridge|flyover|'
                  r'traffic|car|bus|truck|vehicle|motorcycle|carriageway|rajpur|dnd|motorcyclist|pedestrian)\b', re.I)
NOISE = re.compile(r'cricket|stock market|box office|fever|social media|share price|film star', re.I)
PHYSICAL = re.compile(r'\b(?:road|highway|nh\s*-?\s*\d+|bridge|bus|truck|carriageway|flyover)\b', re.I)
INCIDENT = re.compile(r'landslide|rockfall|crash|collision|accident|waterlog|flood|block|clos|'
                      r'diversion|divert|chakka jam|protest|congest|traffic jam|restored|reopen|'
                      r'vehicular movement|washout|washed away|slope failure', re.I)
CLEARED = re.compile(r'traffic (?:has been |was |is )?restored|blockade (?:has been |was )?lifted|'
                     r'opened for vehicular movement|reopened|no longer blocked|traffic (?:is )?normal|'
                     r'(?:road|highway|bridge) (?:has been |was )?cleared', re.I)
FUTURE = re.compile(r'will (?:remain |be )?(?:closed|blocked)|tomorrow|scheduled|next week|planned closure', re.I)
ACTIVE = re.compile(r'remains? (?:closed|blocked)|still (?:closed|blocked)|not (?:yet )?(?:reopened|restored)', re.I)


def teacher_result(item, version='calibrated_teacher-v1'):
    text = text_of(item)
    relevant = bool(ROAD.search(text) and INCIDENT.search(text))
    if NOISE.search(text) and not PHYSICAL.search(text):
        relevant = False
    status = TemporalStatus.UNRELATED_NOISE
    if relevant:
        cleared = [match for match in CLEARED.finditer(text) if not re.search(
            r'not (?:yet )?$', text[max(0, match.start()-12):match.start()], re.I)]
        active = list(ACTIVE.finditer(text))
        if FUTURE.search(text):
            status = TemporalStatus.SCHEDULED_FUTURE
        elif cleared and (not active or cleared[-1].start() > active[-1].start()):
            status = TemporalStatus.CLEARED
        else:
            status = TemporalStatus.ACTIVE
    return SetFitGatekeeperResult(report_id=item.report_id, is_relevant=relevant,
                                  confidence=0.85 if relevant else 0.9,
                                  temporal_status=status, model_version=version)


class GatekeeperModel:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._model = None
        self._lock = threading.Lock()

    def _local(self, item):
        with self._lock:
            if self._model is None:
                from setfit import SetFitModel
                self._model = SetFitModel.from_pretrained(str(self.settings.setfit_model_path), local_files_only=True)
            labels = self._model.predict([text_of(item)])
            label = labels.tolist()[0] if hasattr(labels, 'tolist') else labels[0]
            if not isinstance(label, str):
                label = self._model.labels[int(label)]
            status = TemporalStatus(label)
            if status not in {TemporalStatus.ACTIVE, TemporalStatus.CLEARED,
                              TemporalStatus.SCHEDULED_FUTURE, TemporalStatus.UNRELATED_NOISE}:
                raise ValueError('SetFit artifact has unsupported temporal classes')
            probabilities = self._model.predict_proba([text_of(item)])
            confidence = float(max(probabilities.tolist()[0]))
            return SetFitGatekeeperResult(report_id=item.report_id,
                is_relevant=status != TemporalStatus.UNRELATED_NOISE, confidence=confidence,
                temporal_status=status, model_version=f'local_setfit:{self.settings.setfit_model_path.name}')

    async def classify(self, item):
        local = self.settings.inference_mode in ('local_setfit', 'hybrid_setfit_plus_cloud_api')
        if local and self.settings.setfit_model_path.is_dir():
            try:
                return await asyncio.to_thread(self._local, item)
            except (ImportError, OSError, ValueError, RuntimeError, AttributeError, KeyError, IndexError):
                return teacher_result(item, 'calibrated_teacher-v1:local_load_fallback')
        return teacher_result(item, 'calibrated_teacher-v1:missing_local' if local else 'calibrated_teacher-v1')


@lru_cache
def get_gatekeeper():
    return GatekeeperModel()
