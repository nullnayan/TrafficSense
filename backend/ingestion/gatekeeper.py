# @file: backend/ingestion/gatekeeper.py
# @exports: passes_macro_gatekeeper
# @depends_on: backend/schemas/ingestion.py
# @purpose: Deterministic high-severity roadway pre-filter; no temporal/LLM classification.
import re

from backend.schemas.ingestion import RawIngestedItem

ROAD = re.compile(
    r'\b(?:nh\s*[-–]?\s*\d+|sh\s*[-–]?\s*\d+|road|roads|highway|expressway|'
    r'flyover|bridge|traffic|motorway|car|bus|truck|vehicle|motorcyclist|pedestrian)\b|'
    r'सड़क|राजमार्ग|यातायात|पुल|वाहन', re.I,
)
NOISE = re.compile(r'\b(?:cricket|wicket|box office|stock market|share price|film|celebrity)\b', re.I)
SIGNALS = {
    'fatal_collision': r'\b(?:fatal|deadly|killed|deaths?|died)\b.{0,100}\b(?:crash|collision|accident)\b|'
                       r'\b(?:crash|collision|accident)\b.{0,100}\b(?:fatal|deadly|killed|deaths?|died)\b',
    'landslide': r'\b(?:landslide|rockfall|slope failure|mudslide)\b|भूस्खलन',
    'bridge_failure': r'\bbridge\b.{0,80}\b(?:wash(?:ed)?\s*(?:out|away)|collapse[ds]?|damaged)\b',
    'blockade': r'\b(?:chakka\s+jam|highway blockade|road blockade)\b|चक्का\s*जाम',
    'severe_waterlogging': r'\b(?:severe|heavy|deep|massive)\s+(?:rain\s+)?waterlogging\b|'
                          r'\b(?:submerged|inundated|flooded)\b|जलभराव',
    'closure_diversion': r'\b(?:closed|closure|blocked|blockade|diversion|diverted|impassable)\b|'
                         r'मार्ग\s*बंद|सड़क\s*बंद',
}
PATTERNS = [(reason, re.compile(pattern, re.I)) for reason, pattern in SIGNALS.items()]
PHYSICAL = re.compile(r'\b(?:highway|road|bridge|nh\s*-?\s*\d+|sh\s*-?\s*\d+|crash|collision)\b|सड़क|राजमार्ग', re.I)


def passes_macro_gatekeeper(item: RawIngestedItem) -> tuple[bool, str]:
    # Weather annotations are context, never evidence of an incident.
    content = f'{item.title}\n{item.raw_text.split("[weather_context", 1)[0]}'
    if NOISE.search(content) and not PHYSICAL.search(content):
        return False, 'non_traffic_noise'
    if not ROAD.search(content):
        return False, 'no_roadway_context'
    for reason, pattern in PATTERNS:
        if pattern.search(content):
            return True, reason
    return False, 'below_macro_severity'
