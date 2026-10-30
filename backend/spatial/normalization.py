# @file: backend/spatial/normalization.py
# @exports: normalize, query_candidates, state_names
# @depends_on: backend/schemas/extraction.py
# @purpose: Indian place abbreviations, composite lookup variants, and regional state-code aliases.
import re
import unicodedata

STATE_CODES = {'39': 'uttarakhand', '07': 'delhi', '10': 'haryana', '36': 'uttar pradesh'}
CITIES = {'greater noida': 'Uttar Pradesh', 'dehradun': 'Uttarakhand', 'rishikesh': 'Uttarakhand',
          'noida': 'Uttar Pradesh', 'delhi': 'Delhi', 'gurugram': 'Haryana', 'bengaluru': 'Karnataka',
          'patna': 'Bihar', 'jammu': 'Jammu and Kashmir', 'chennai': 'Tamil Nadu'}


def normalize(value):
    value = unicodedata.normalize('NFKD', value or '').casefold()
    for short, full in [('nr', 'near'), ('opp', 'opposite'), ('twr', 'tower'), ('rd', 'road'),
                        ('lakshman', 'laxman'), ('gurgaon', 'gurugram'), ('bangalore', 'bengaluru')]:
        value = re.sub(rf'\b{short}\b', full, value)
    value = re.sub(r'\b(nh|sh)\s*[-–]?\s*(\d+)', r'\1 \2', value)
    return ' '.join(re.sub(r'[^\w\s]', ' ', value).split())


def state_names(state):
    value = normalize(state)
    names = {value, STATE_CODES.get(value, value)}
    names.update(code for code, name in STATE_CODES.items() if name == value)
    return sorted(names)


def contextualize(landmark):
    if landmark.city:
        return landmark
    text = normalize(' '.join(filter(None, [landmark.primary_landmark, landmark.locality])))
    city = next((name for name in CITIES if re.search(rf'\b{name}\b', text)), None)
    return landmark.model_copy(update={'city': city.title(), 'state': landmark.state or CITIES[city]}) if city else landmark


def city_names(city):
    value = normalize(city)
    aliases = {'noida': ['gautam buddha nagar'], 'greater noida': ['gautam buddha nagar'],
               'rishikesh': ['dehradun', 'tehri garhwal', 'pauri garhwal'], 'delhi': ['new delhi', 'central delhi']}
    return [value, *aliases.get(value, [])]


def query_candidates(landmark):
    values = [landmark.primary_landmark, landmark.locality]
    primary = normalize(landmark.primary_landmark)
    # Preserve a distinctive landmark anchor even when colloquial directions include nearby roads.
    anchors = re.findall(r'\b(?:clock tower|laxman jhula|kali mandir|silk board|rajiv chowk)\b', primary)
    values.extend(anchors)
    values.append(' '.join(filter(None, [landmark.primary_landmark, landmark.corridor_name,
                                        landmark.locality, landmark.city])))
    cleaned = re.sub(r'\b(?:near|opposite|beside|at|on)\b', '', primary)
    values.append(cleaned)
    # A corridor or city alone is too broad to fabricate a point incident location.
    return list(dict.fromkeys(normalize(value) for value in values if value and normalize(value)))
