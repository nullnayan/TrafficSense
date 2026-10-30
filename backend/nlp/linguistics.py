# @file: backend/nlp/linguistics.py
# @exports: text_of, lane_evidence, location_evidence, incident_evidence
# @depends_on: backend/schemas/extraction.py
# @purpose: Conservative Indian incident, lane-number, corridor, and colloquial landmark evidence.
import re

from backend.schemas.enums import IncidentType, Severity
from backend.schemas.extraction import LandmarkTuple

WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
         'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
NUM = r'(?:\d{1,2}|' + '|'.join(WORDS) + r')'


def text_of(item):
    return f'{item.title}. {item.raw_text.split("[weather_context", 1)[0]}'


def lane_evidence(text):
    def number(value):
        return WORDS[value.lower()] if value.lower() in WORDS else int(value)
    blocked, total = None, None
    pattern = rf'\b({NUM})\s+(?:(?:of|out of)\s+({NUM})\s+)?lanes?\s+(?:(?:are|were|remain|is)\s+)?(?:blocked|closed|obstructed)\b'
    match = re.search(pattern, text, re.I)
    if match and not re.search(r'(?:at least|up to|about|around|approximately)\s*$', text[max(0, match.start()-20):match.start()], re.I):
        blocked = number(match[1])
        total = number(match[2]) if match[2] else None
    reverse = re.search(rf'\b(?:blocks?|blocking|closed|obstructs?)\s+({NUM})\s+lanes?\b', text, re.I)
    if blocked is None and reverse:
        blocked = number(reverse[1])
    road = re.search(rf'\b({NUM})[- ]lane\s+(?:road|highway|bridge|carriageway)\b', text, re.I)
    if road:
        total = number(road[1])
    all_lanes = re.search(rf'\ball\s+({NUM})\s+lanes?\s+(?:blocked|closed)\b', text, re.I)
    if all_lanes:
        blocked = total = number(all_lanes[1])
    if blocked is not None and not 0 <= blocked <= 30:
        blocked = None
    if total is not None and not 1 <= total <= 30:
        total = None
    if blocked is not None and total is not None and blocked > total:
        blocked = total = None
    return blocked, total


def location_evidence(text):
    corridor = re.search(r'\b(NH|SH)\s*[-–]?\s*(\d+[A-Z]?)\b', text, re.I)
    name = f'{corridor[1].upper()}-{corridor[2].upper()}' if corridor else None
    if not name:
        match = re.search(r'\b(Rajpur Road|DND Flyway|Dehradun-Rishikesh Highway)\b', text, re.I)
        name = match[0] if match else None
    landmark = re.search(
        r'\b(?:near|opposite|at|beside)\s+([\w\-]+(?:\s+[\w\-]+){0,4}?)'
        r'(?=\s+(?:on|in|after|due|towards|toward|with|as|where|is|has)\b|[.,;]|$)', text, re.I,
    )
    primary = landmark[1].strip() if landmark else None
    if not primary:
        match = re.search(r'\b(?:[A-Z][\w-]*\s+){1,3}(?:Chowk|Bridge|Flyover|Tunnel|Jhula)\b', text)
        primary = match[0] if match else None
    cities = {'dehradun': 'Uttarakhand', 'rishikesh': 'Uttarakhand', 'bengaluru': 'Karnataka',
              'delhi': 'Delhi', 'noida': 'Uttar Pradesh', 'gurugram': 'Haryana', 'patna': 'Bihar',
              'mumbai': 'Maharashtra', 'chennai': 'Tamil Nadu', 'jammu': 'Jammu and Kashmir'}
    city = next((city for city in cities if re.search(rf'\b{city}\b', text, re.I)), None)
    return LandmarkTuple(corridor_name=name, primary_landmark=primary,
                         city=city.title() if city else None, state=cities.get(city))


def incident_evidence(text):
    kinds = [(r'landslide|rockfall|slope failure|mudslide', IncidentType.LANDSLIDE),
             (r'bridge.{0,40}(?:washed|collapse|washout)', IncidentType.BRIDGE_DAMAGE),
             (r'crash|collision|accident', IncidentType.COLLISION),
             (r'waterlog|flood|submerged|inundat', IncidentType.WATERLOGGING),
             (r'chakka jam|protest|demonstrat|blockade', IncidentType.PROTEST),
             (r'roadwork|construction|maintenance', IncidentType.ROADWORK),
             (r'closed|closure|blocked|diversion', IncidentType.ROAD_CLOSURE),
             (r'congest|traffic jam', IncidentType.CONGESTION)]
    kind = next((kind for pattern, kind in kinds if re.search(pattern, text, re.I)), IncidentType.UNKNOWN)
    severity = Severity.UNKNOWN
    if re.search(r'\bfatal|\bkilled|\bdeadly|washout|washed away|bridge collapse', text, re.I):
        severity = Severity.CATASTROPHIC
    elif re.search(r'\bsevere|\bmajor|\bheavy|all lanes|impassable', text, re.I):
        severity = Severity.SEVERE
    elif re.search(r'\bminor', text, re.I):
        severity = Severity.MINOR
    return kind, severity
