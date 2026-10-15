"""Shared test setup.

Puts src/ on the path and, only if `rapidfuzz` isn't installed, registers a
small stand-in built on the standard library's difflib. On a normal setup
(`pip install -e .`) the real rapidfuzz is used and this does nothing.
The stand-in follows rapidfuzz's documented algorithms but its scores can
differ by a few points, which is why the tests assert behaviour (matched or
not, and why), not exact scores.
"""

import sys
import types
from difflib import SequenceMatcher
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 100.0
    return SequenceMatcher(None, a, b).ratio() * 100


def _token_sort_ratio(a: str, b: str) -> float:
    return _ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))


def _token_set_ratio(a: str, b: str) -> float:
    tokens_a, tokens_b = set(a.split()), set(b.split())
    common = " ".join(sorted(tokens_a & tokens_b))
    only_a = " ".join(sorted(tokens_a - tokens_b))
    only_b = " ".join(sorted(tokens_b - tokens_a))
    if not common:
        return _ratio(only_a, only_b)
    with_a = f"{common} {only_a}".strip()
    with_b = f"{common} {only_b}".strip()
    return max(_ratio(common, with_a), _ratio(common, with_b), _ratio(with_a, with_b))


def install_rapidfuzz_stand_in_if_missing() -> bool:
    try:
        import rapidfuzz  # noqa: F401
        return False
    except ImportError:
        pass
    fuzz = types.SimpleNamespace(ratio=_ratio, token_sort_ratio=_token_sort_ratio, token_set_ratio=_token_set_ratio)
    module = types.ModuleType("rapidfuzz")
    module.fuzz = fuzz
    sys.modules["rapidfuzz"] = module
    return True


USING_STAND_IN = install_rapidfuzz_stand_in_if_missing()
