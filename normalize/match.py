"""Conservative title matching: incomplete identities never confirm equivalence."""
import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Optional

@dataclass
class ParsedMedicine:
    raw_title: str
    brand_or_molecule: str
    strength: Optional[str]
    pack_size: Optional[str]
    form: Optional[str]

FORM_WORDS = {
    'tablet': r'\b(?:tablets?|tabs?)\b',
    'capsule': r'\b(?:capsules?|caps?)\b',
    'syrup': r'\bsyrup\b',
    'suspension': r'\bsuspension\b',
    'injection': r'\b(?:injections?|inj)\b',
    'cream': r'\bcream\b',
    'ointment': r'\bointment\b',
    'gel': r'\bgel\b',
    'drops': r'\bdrops?\b',
    'sachet': r'\bsachets?\b',
}
STRENGTH = re.compile(r'(\d+(?:\.\d+)?)\s*(mcg|mg|gm|g|iu|%)(?![a-z])', re.I)
PACK = [r"(\d+)\s*['’]?s\b", r'(?:strip|pack)\s+of\s+(\d+)',
        r'\bx\s*(\d+)\b', r'(\d+)\s*(?:tablets?|tabs?|capsules?|caps?|sachets?)(?:\(s\))?\b']

def parse_listing_title(title: str) -> ParsedMedicine:
    lower = title.lower().strip()
    form = next((f for f, pattern in FORM_WORDS.items() if re.search(pattern, lower)), None)
    # Every explicit strength is retained, including combination ingredients.
    strengths = list(STRENGTH.finditer(lower))
    strength = '|'.join(m.group(1) + m.group(2) for m in strengths) or None
    masked = STRENGTH.sub(lambda m: ' ' * len(m.group()), lower)
    pack_size = None
    pack_start = len(lower)
    for pattern in PACK:
        matches = list(re.finditer(pattern, masked))
        if matches:
            match = matches[-1]
            # A leading strength such as Dolo 650 Tablet is not a pack count.
            if pattern == PACK[-1] and not strengths and len(re.findall(r'\d+', masked)) == 1:
                continue
            pack_size, pack_start = match.group(1), match.start()
            break
    # Liquid/topical volume is pack information, never a dosage strength.
    volume = re.search(r'\b(\d+(?:\.\d+)?)\s*ml\b', lower)
    if volume:
        pack_size = volume.group(1) + 'ml'
        pack_start = min(pack_start, volume.start())
    if not strength:
        bare = re.search(r'\d+(?:\.\d+)?', lower[:pack_start])
        if bare:
            strength = bare.group()
    # Keep suffixes (SR, Plus, etc.) and exact brand identity. Strip only known
    # form/pack/strength syntax; similarity must never erase a variant name.
    brand = lower[:pack_start]
    brand = STRENGTH.sub(' ', brand)
    brand = re.sub(r'(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])', ' ', brand)
    for pattern in FORM_WORDS.values():
        brand = re.sub(pattern, ' ', brand)
    brand = re.sub(r'\(s\)', ' ', brand)
    brand = re.sub(r'[^a-z0-9]+', '', brand)
    # Concentrations cannot safely be inferred by this title-only matcher.
    if re.search(r'/\s*(?:\d+(?:\.\d+)?\s*)?(?:ml|g|gm)\b', lower):
        strength = None
    return ParsedMedicine(title.strip(), brand, strength, pack_size, form)

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def _strength_key(value):
    keys = []
    for component in value.split('|'):
        m = re.fullmatch(r'(\d+(?:\.\d+)?)(mcg|mg|gm|g|iu|%)?', component)
        if not m:
            return None
        number, unit = Decimal(m[1]), m[2]
        # Preserve units: 10mg and 10mcg must never compare equal.
        if unit in ('g', 'gm'):
            number, unit = number * 1000, 'mg'
        elif unit == 'mcg':
            number, unit = number / 1000, 'mg'
        keys.append((number, unit))
    return keys

def match_score(a: ParsedMedicine, b: ParsedMedicine) -> float:
    if not a.brand_or_molecule or a.brand_or_molecule != b.brand_or_molecule:
        return 0.0
    if not a.form or a.form != b.form or not a.pack_size or a.pack_size != b.pack_size:
        return 0.0
    if not a.strength or not b.strength:
        return 0.0
    left, right = _strength_key(a.strength), _strength_key(b.strength)
    if not left or not right or len(left) != len(right):
        return 0.0
    for (lv, lu), (rv, ru) in zip(left, right):
        if lv != rv or lu != ru:
            return 0.0
    # A bare number cannot confirm a dosage unit, even against another bare number.
    return 1.0 if all(unit is not None for _, unit in left) else 0.0

def best_canonical_match(parsed, candidates: list[dict], threshold: float = 0.80):
    best_id, best_score = None, 0.0
    for candidate in candidates:
        score = match_score(parsed, parse_listing_title(candidate['canonical_name']))
        if score > best_score:
            best_id, best_score = candidate['id'], score
    return (best_id, best_score) if best_score >= threshold else (None, best_score)
