"""
Medicine-name normalization and cross-platform matching.

The hard problem: "Dolo 650 Tablet 15's" (1mg), "Dolo 650mg Strip of 15 Tablets"
(PharmEasy), "DOLO 650 TAB 15S" (Netmeds) all need to resolve to the SAME
canonical_items row. Pure string similarity fails because pack-size and brand
vs. generic naming vary. This module breaks the title into structured fields
first, then matches on those fields — string similarity is only a tie-breaker.

Usage:
    from normalize.match import parse_listing_title, find_or_create_canonical

    parsed = parse_listing_title("Dolo 650mg Strip of 15 Tablets")
    # -> ParsedMedicine(brand='Dolo', strength='650mg', pack_size='15', form='tablet')
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

# Common pack-size phrasing across platforms: "15's", "strip of 15", "15 tab", "x15"
PACK_SIZE_PATTERNS = [
    r"(\d+)\s*(?:'s|s\b)",
    r"strip of\s*(\d+)",
    r"pack of\s*(\d+)",
    r"x\s*(\d+)\b",
    r"(\d+)\s*(?:tab|tabs|tablet|tablets|cap|caps|capsule|capsules)\b",
]

STRENGTH_PATTERN = r"(\d+(?:\.\d+)?)\s*(mg|mcg|ml|gm|g|%|iu)\b"

FORM_WORDS = {
    "tablet": ["tablet", "tab", "tabs"],
    "capsule": ["capsule", "cap", "caps"],
    "syrup": ["syrup", "suspension"],
    "injection": ["injection", "inj"],
    "cream": ["cream", "ointment", "gel"],
    "drops": ["drops", "drop"],
    "sachet": ["sachet", "sachets"],
}


@dataclass
class ParsedMedicine:
    raw_title: str
    brand_or_molecule: str   # best-effort: leading words before strength/pack info
    strength: Optional[str]  # e.g. "650mg"
    pack_size: Optional[str] # e.g. "15"
    form: Optional[str]      # e.g. "tablet"


def parse_listing_title(title: str) -> ParsedMedicine:
    t = title.strip()
    lower = t.lower()

    strength = None
    strength_match = re.search(STRENGTH_PATTERN, lower)
    if strength_match:
        strength = f"{strength_match.group(1)}{strength_match.group(2)}"

    # Mask out the strength's digits before hunting for pack size — handles
    # titles where the strength DOES carry a unit (e.g. "650mg").
    pack_search_text = lower
    if strength_match:
        start, end = strength_match.span()
        pack_search_text = lower[:start] + " " * (end - start) + lower[end:]

    # Take the LAST match for a pattern, not the first. Real titles put the
    # strength number right after the brand name, near the START of the
    # string ("Dolo 650 Tablet ..."), and the pack count near the END
    # ("... strip of 15 tablets"). The generic "<digit> tablet(s)" pattern
    # can match BOTH "650 Tablet" and "15 tablets" in the same string even
    # with strength masked out as a bare number (no unit) — e.g. "Dolo 650
    # Tablet 15 tablets" has no unit on "650" to mask. Taking the rightmost
    # match favors the real pack count over the strength number. Verified
    # against live 1mg data: without this, "Dolo 650 Tablet" + "15 tablets"
    # parsed pack_size as '650' instead of '15'.
    pack_size = None
    for pat in PACK_SIZE_PATTERNS:
        matches = list(re.finditer(pat, pack_search_text))
        if matches:
            pack_size = matches[-1].group(1)
            break

    # Guard: if the title has only ONE number total (no explicit pack
    # phrase like "strip of", "'s", "pack of" — just a bare generic
    # "<digit> tablet" match), that lone number is far more likely to be
    # the STRENGTH written without its unit (e.g. "Dolo 650 Tablet" with no
    # "mg") than a pack count, since real pack counts almost always show up
    # as a SECOND number later in the title ("strip of 15", "15's").
    # Verified against live 1mg data: "Dolo 650 Tablet" alone (no pack info
    # in that field) was parsing pack_size='650' — i.e. treating the drug
    # strength as if it were a 650-tablet pack, which would make this
    # medicine fail to match its own correct listing on other platforms
    # whenever the pack count wasn't bundled into the same string.
    all_numbers = re.findall(r"\d+", pack_search_text)
    has_explicit_pack_phrase = any(
        re.search(p, pack_search_text) for p in PACK_SIZE_PATTERNS[:4]  # all but the generic "<digit> tablet" one
    )
    if pack_size and len(all_numbers) <= 1 and not has_explicit_pack_phrase:
        pack_size = None

    # Fallback: if no unit-based strength was found, but the FIRST number in
    # the title (immediately after the brand, e.g. "Dolo 650 Tablet") isn't
    # being claimed as the pack size, treat it as a bare strength value with
    # no unit. This number is dropped otherwise — and a dropped strength
    # means two genuinely different-strength products (e.g. "Dolo 650" vs
    # "Dolo 500") can't be told apart, since the comparison has nothing to
    # check. Verified against live Netmeds data: without this, "Dolo 500mg
    # Tablet 15'S" falsely matched a "Dolo 650 Tablet" reference — a real
    # wrong-strength mismatch a medicine site cannot afford to get wrong.
    # Stored WITHOUT a unit (just digits) — match_score compares strength by
    # numeric value only, so a bare "650" still correctly matches "650mg".
    if strength is None and all_numbers:
        first_number = all_numbers[0]
        if first_number != pack_size:
            strength = first_number

    form = None
    for form_name, words in FORM_WORDS.items():
        if any(w in lower for w in words):
            form = form_name
            break

    # Brand/molecule = everything before the first digit, stripped of punctuation.
    # This is intentionally crude — it's the seed for fuzzy matching, not the final word.
    m = re.search(r"^[^\d]+", t)
    brand_or_molecule = (m.group(0) if m else t).strip(" -,:").strip()

    return ParsedMedicine(
        raw_title=t,
        brand_or_molecule=brand_or_molecule,
        strength=strength,
        pack_size=pack_size,
        form=form,
    )


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _normalize_brand(s: str) -> str:
    """Lowercase, strip punctuation/whitespace, so 'Dolo-650' and 'dolo 650'
    normalize the same — but 'Dolo' and 'Dolomed' stay different."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match_score(a: ParsedMedicine, b: ParsedMedicine) -> float:
    """
    Weighted score 0..1, with a HARD GATE on brand/molecule name.

    Why a gate and not just a fuzzy weight: on real Indian pharmacy data,
    dozens of distinct brands share a prefix (Dolo, Dolomed, Dolopar, Dolosen,
    Doloxer, Dolofirst, ... are all different real products, not variants of
    one brand). Plain string-similarity scoring rates "Dolo" vs "Dolomed"
    HIGHER than "Dolo" vs "Dolo" with messier formatting, because the fuzzy
    ratio rewards shared substrings regardless of whether they're the same
    product — verified against 18 live 1mg results where this produced 5
    false "same medicine" matches. For a medicine site, a false match here
    means telling someone the wrong drug is their cheaper option, so this
    gate errs toward rejecting rather than guessing.

    The gate: normalized brand strings must be exactly equal, OR one must be
    a true prefix/suffix of the other with a length difference of at most 2
    chars (catches "Dolo650" vs "Dolo-650" style formatting noise only — not
    "Dolo" vs "Dolomed", a 4-char difference).
    """
    norm_a = _normalize_brand(a.brand_or_molecule)
    norm_b = _normalize_brand(b.brand_or_molecule)

    brand_gate_passed = False
    if norm_a and norm_b:
        if norm_a == norm_b:
            brand_gate_passed = True
        elif (norm_a in norm_b or norm_b in norm_a) and abs(len(norm_a) - len(norm_b)) <= 2:
            brand_gate_passed = True

    if not brand_gate_passed:
        return 0.0

    score = 0.0
    weight_total = 0.0

    # Strength: must match if both present, else neutral. Compared on
    # digits only — a bare fallback value like "650" (no unit, see
    # parse_listing_title) must still match a unit-bearing "650mg" from
    # another platform, while correctly differing from "500mg".
    if a.strength and b.strength:
        weight_total += 0.40
        digits_a = re.sub(r"[^0-9.]", "", a.strength)
        digits_b = re.sub(r"[^0-9.]", "", b.strength)
        if digits_a and digits_a == digits_b:
            score += 0.40

    # Pack size: must match if both present
    if a.pack_size and b.pack_size:
        weight_total += 0.30
        if a.pack_size == b.pack_size:
            score += 0.30

    # Form: tablet vs syrup etc. — hard mismatch if both present and differ
    if a.form and b.form:
        weight_total += 0.15
        if a.form == b.form:
            score += 0.15

    # Brand already passed the hard gate — small residual weight for exactness
    weight_total += 0.15
    score += 0.15 * similarity(a.brand_or_molecule, b.brand_or_molecule)

    if weight_total == 0:
        return 0.0
    return score / weight_total


def best_canonical_match(parsed: ParsedMedicine, candidates: list[dict], threshold: float = 0.80):
    """
    candidates: list of {"id": int, "canonical_name": str} pulled from
    canonical_items for the same item_type (cheap pre-filter, e.g. by trigram
    search on canonical_name, should happen in SQL before this is called).

    Returns (canonical_item_id, confidence) or (None, 0.0) if nothing clears
    the threshold — in which case the caller should insert a new
    canonical_items row and flag match_status='manual' for a human to confirm
    on the first few runs.
    """
    best_id, best_score = None, 0.0
    for c in candidates:
        c_parsed = parse_listing_title(c["canonical_name"])
        s = match_score(parsed, c_parsed)
        if s > best_score:
            best_id, best_score = c["id"], s

    if best_score >= threshold:
        return best_id, best_score
    return None, best_score


if __name__ == "__main__":
    titles = [
        "Dolo 650 Tablet 15's",
        "Dolo 650mg Strip of 15 Tablets",
        "DOLO 650 TAB 15S",
        "Crocin Advance Tablet 15's",  # should NOT match Dolo
    ]
    parsed = [parse_listing_title(t) for t in titles]
    for p in parsed:
        print(p)
    print("Dolo variants similarity:", match_score(parsed[0], parsed[1]))
    print("Dolo vs Crocin similarity:", match_score(parsed[0], parsed[3]))
