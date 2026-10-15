"""Matches equivalent products between two platforms' result lists.

Pure functions, no network calls, so the logic can be unit-tested with
hand-written data (see tests/).

Two products are the same product only if they pass every gate, in order:

1. Brand       -- different brands are never the same product.
2. Pack size   -- the same product has the same pack size on both
                  platforms (10% tolerance). A 100 g bar and a 125 g bar
                  are different products and shouldn't be price-compared.
3. Name        -- the canonical simple_name (see models.py) must be
                  similar enough on its own. Size and price can never make
                  up for a name that doesn't match.
4. Price sanity -- a gap above 3x almost certainly means different products.

Price is deliberately NOT part of the match score. Using price similarity
to decide matches would hide exactly the pairs this tool exists to find:
the ones with a big price gap.

Products that pass every gate get a score (name similarity, nudged by size
similarity) and are paired best-score-first across both lists, so the
result doesn't depend on the order products happened to be listed in.
Everything that isn't paired is reported with its closest candidate on the
other platform and the reason it was rejected, so the matching is
explainable rather than a black box.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

BRAND_MATCH_THRESHOLD = 80
NAME_MIN_SCORE = 60.0
MATCH_THRESHOLD = 70.0
SIZE_TOLERANCE = 0.10
MAX_PRICE_RATIO = 3.0

NAME_WEIGHT = 0.75
SIZE_WEIGHT = 0.25

_UNIT_FACTORS = {
    "kg": (1000.0, "mass"),
    "g": (1.0, "mass"),
    "gm": (1.0, "mass"),
    "gms": (1.0, "mass"),
    "gram": (1.0, "mass"),
    "grams": (1.0, "mass"),
    "ml": (1.0, "volume"),
    "l": (1000.0, "volume"),
    "ltr": (1000.0, "volume"),
    "litre": (1000.0, "volume"),
    "litres": (1000.0, "volume"),
    "liter": (1000.0, "volume"),
    "liters": (1000.0, "volume"),
}
_UNIT_PATTERN = "kg|gms|gm|gram|grams|g|ml|litres|litre|liters|liter|ltr|l"
_MULTI_PACK_RE = re.compile(rf"(\d+)\s*(?:x|×)\s*(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})\b")
_SINGLE_PACK_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT_PATTERN})\b")
_COUNT_RE = re.compile(r"(?:pack of|set of)\s*(\d+)|(\d+)\s*(?:pcs|pc|pieces|piece|units|unit)\b")


@dataclass(frozen=True)
class Evaluation:
    """How one Blinkit/Zepto candidate pair fared against the gates."""

    name_score: float
    size_score: Optional[float]
    brand_ok: bool
    score: float
    reject_reason: Optional[str]  # None means every gate passed

    @property
    def passes_gates(self) -> bool:
        return self.reject_reason is None


def normalize(text: Optional[str]) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_pack_size(text: Optional[str]) -> Optional[tuple[float, str]]:
    """Returns (amount, kind) with kind in {"mass", "volume", "count"}.

    Mass is in grams, volume in millilitres, count in items, so '3 x 100 g'
    and '300 g' compare equal. None when no pack size can be read.
    """
    if not text:
        return None
    text = text.lower()

    multi = _MULTI_PACK_RE.search(text)
    if multi:
        factor, kind = _UNIT_FACTORS[multi.group(3)]
        return float(multi.group(1)) * float(multi.group(2)) * factor, kind

    single = _SINGLE_PACK_RE.search(text)
    if single:
        factor, kind = _UNIT_FACTORS[single.group(2)]
        return float(single.group(1)) * factor, kind

    count = _COUNT_RE.search(text)
    if count:
        return float(count.group(1) or count.group(2)), "count"

    return None


def _product_pack_size(product: dict) -> Optional[tuple[float, str]]:
    return parse_pack_size(product.get("quantity")) or parse_pack_size(product.get("name"))


def price_per_unit(product: dict) -> Optional[tuple[float, str]]:
    """Price per 100 g / 100 ml / item, so different pack sizes can be compared fairly."""
    price = product.get("price")
    size = _product_pack_size(product)
    if not isinstance(price, (int, float)) or not size or size[0] <= 0:
        return None
    amount, kind = size
    if kind == "count":
        return round(price / amount, 2), "item"
    unit = "100 g" if kind == "mass" else "100 ml"
    return round(price / amount * 100, 2), unit


def _brands_are_compatible(product_a: dict, product_b: dict) -> bool:
    """Missing brand data on either side can't rule a match out."""
    brand_a = normalize(product_a.get("brand"))
    brand_b = normalize(product_b.get("brand"))
    if not brand_a or not brand_b:
        return True
    if brand_a == brand_b or f" {brand_a} " in f" {brand_b} " or f" {brand_b} " in f" {brand_a} ":
        return True
    return fuzz.ratio(brand_a, brand_b) >= BRAND_MATCH_THRESHOLD


def _comparable_name(product: dict) -> str:
    """Prefers the LLM's canonical simple_name; falls back to the full name."""
    return normalize(product.get("simple_name") or product.get("name"))


def _name_similarity(product_a: dict, product_b: dict) -> float:
    """Average of token-set and token-sort similarity.

    Token-set alone scores 100 whenever one name is contained in the other,
    which would treat 'shampoo' and 'shampoo anti dandruff' as identical.
    Averaging in token-sort penalises those extra variant words.
    """
    name_a, name_b = _comparable_name(product_a), _comparable_name(product_b)
    return (fuzz.token_set_ratio(name_a, name_b) + fuzz.token_sort_ratio(name_a, name_b)) / 2


def _size_comparison(product_a: dict, product_b: dict) -> tuple[Optional[float], bool]:
    """(similarity 0-100 or None if unknown, whether the sizes are acceptably close)."""
    size_a, size_b = _product_pack_size(product_a), _product_pack_size(product_b)
    if not size_a or not size_b or size_a[1] != size_b[1]:
        return None, True
    relative_gap = abs(size_a[0] - size_b[0]) / max(size_a[0], size_b[0])
    if relative_gap > SIZE_TOLERANCE:
        return 0.0, False
    return 100.0 * (1 - relative_gap / SIZE_TOLERANCE * 0.2), True


def evaluate_pair(blinkit_product: dict, zepto_product: dict) -> Evaluation:
    name_score = _name_similarity(blinkit_product, zepto_product)
    size_score, size_ok = _size_comparison(blinkit_product, zepto_product)
    brand_ok = _brands_are_compatible(blinkit_product, zepto_product)

    reject_reason = None
    if not brand_ok:
        reject_reason = "different brand"
    elif not size_ok:
        reject_reason = "different pack size"
    elif name_score < NAME_MIN_SCORE:
        reject_reason = "product name too different"
    else:
        price_a, price_b = blinkit_product.get("price"), zepto_product.get("price")
        if isinstance(price_a, (int, float)) and isinstance(price_b, (int, float)) and price_a > 0 and price_b > 0:
            if max(price_a, price_b) / min(price_a, price_b) > MAX_PRICE_RATIO:
                reject_reason = "price gap too large to be the same product"

    if size_score is None:
        score = name_score
    else:
        score = NAME_WEIGHT * name_score + SIZE_WEIGHT * size_score

    if reject_reason is None and score < MATCH_THRESHOLD:
        reject_reason = "overall similarity below threshold"

    return Evaluation(name_score, size_score, brand_ok, score, reject_reason)


def _build_pair(blinkit_product: dict, zepto_product: dict, evaluation: Evaluation) -> dict:
    blinkit_price, zepto_price = blinkit_product.get("price"), zepto_product.get("price")
    cheaper, price_diff, diff_percent = "tie", 0.0, 0.0
    if blinkit_price < zepto_price:
        cheaper = "blinkit"
    elif zepto_price < blinkit_price:
        cheaper = "zepto"
    if cheaper != "tie":
        price_diff = round(abs(blinkit_price - zepto_price), 2)
        diff_percent = round(price_diff / max(blinkit_price, zepto_price) * 100, 1)

    blinkit_unit, zepto_unit = price_per_unit(blinkit_product), price_per_unit(zepto_product)
    unit_price = None
    if blinkit_unit and zepto_unit and blinkit_unit[1] == zepto_unit[1]:
        unit_price = {"blinkit": blinkit_unit[0], "zepto": zepto_unit[0], "unit": blinkit_unit[1]}

    return {
        "blinkit": blinkit_product,
        "zepto": zepto_product,
        "match_confidence": round(evaluation.score, 1),
        "cheaper_platform": cheaper,
        "price_diff": price_diff,
        "price_diff_percent": diff_percent,
        "unit_price": unit_price,
        "breakdown": {
            "brand": "match" if normalize(blinkit_product.get("brand")) and normalize(zepto_product.get("brand")) else "unknown",
            "name": round(evaluation.name_score, 1),
            "size": None if evaluation.size_score is None else round(evaluation.size_score, 1),
        },
    }


def _closest_candidate(product: dict, others: list[dict], product_is_blinkit: bool, matched_elsewhere: set[int]) -> Optional[dict]:
    """The most similar listing on the other platform, and why it wasn't a match."""
    best = None
    for index, other in enumerate(others):
        evaluation = evaluate_pair(product, other) if product_is_blinkit else evaluate_pair(other, product)
        # Prefer same-brand candidates; among those, the most similar name.
        if best is None or (evaluation.brand_ok, evaluation.name_score) > (best[1].brand_ok, best[1].name_score):
            best = (index, evaluation, other)
    if best is None:
        return None

    index, evaluation, other = best
    if evaluation.reject_reason:
        reason = evaluation.reject_reason
    elif index in matched_elsewhere:
        reason = "its best match was already paired with another listing"
    else:
        reason = "no match"
    return {"name": other.get("name"), "name_score": round(evaluation.name_score, 1), "reason": reason}


def match_products(blinkit_items: list[dict], zepto_items: list[dict]) -> tuple[list, list, list]:
    candidates = []
    for blinkit_index, blinkit_product in enumerate(blinkit_items):
        for zepto_index, zepto_product in enumerate(zepto_items):
            evaluation = evaluate_pair(blinkit_product, zepto_product)
            if evaluation.passes_gates:
                candidates.append((evaluation.score, blinkit_index, zepto_index, evaluation))

    # Best score first; ties broken by list position so results are stable.
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))

    used_blinkit: set[int] = set()
    used_zepto: set[int] = set()
    matched = []
    for _, blinkit_index, zepto_index, evaluation in candidates:
        if blinkit_index in used_blinkit or zepto_index in used_zepto:
            continue
        used_blinkit.add(blinkit_index)
        used_zepto.add(zepto_index)
        pair = _build_pair(blinkit_items[blinkit_index], zepto_items[zepto_index], evaluation)
        logger.debug(
            "matched %r <-> %r | score=%.1f cheaper=%s",
            pair["blinkit"].get("name"), pair["zepto"].get("name"), evaluation.score, pair["cheaper_platform"],
        )
        matched.append(pair)

    blinkit_only, zepto_only = [], []
    for index, product in enumerate(blinkit_items):
        if index not in used_blinkit:
            closest = _closest_candidate(product, zepto_items, True, used_zepto)
            blinkit_only.append({**product, "closest": closest})
    for index, product in enumerate(zepto_items):
        if index not in used_zepto:
            closest = _closest_candidate(product, blinkit_items, False, used_blinkit)
            zepto_only.append({**product, "closest": closest})

    logger.info(
        "matching finished | blinkit_in=%d zepto_in=%d matched=%d blinkit_only=%d zepto_only=%d",
        len(blinkit_items), len(zepto_items), len(matched), len(blinkit_only), len(zepto_only),
    )
    return matched, blinkit_only, zepto_only
