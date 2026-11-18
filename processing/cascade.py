from collections.abc import Mapping
from fractions import Fraction

from processing.result_rows import MissingRow, ValueRow
from source.sample_map import parse_rational


MIN_STAGE_COUNT = 2
MAX_STAGE_COUNT = 6


def normalize_stages(stages):
    """Freeze 2..6 ordered filter stages into sorted nonzero rational taps."""
    if not isinstance(stages, (list, tuple)):
        raise ValueError('stages must be a list or tuple of 2 to 6 filter stages')
    if not MIN_STAGE_COUNT <= len(stages) <= MAX_STAGE_COUNT:
        raise ValueError('expected 2 to 6 filter stages')
    frozen = []
    for stage in stages:
        entries = stage.items() if isinstance(stage, Mapping) else stage
        try:
            entries = list(entries)
        except TypeError as exc:
            raise ValueError('each stage must map integer offsets to rational weights') from exc
        weights = {}
        for entry in entries:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                raise ValueError('expected an offset and a weight pair')
            offset, weight = entry
            if type(offset) is not int or offset in weights:
                raise ValueError('invalid or duplicate stage offset')
            value = parse_rational(weight)
            if value == 0:
                raise ValueError('stage weights must be nonzero')
            weights[offset] = value
        frozen.append(tuple(sorted(weights.items())))
    return tuple(frozen)


def _identity_layer(length):
    return tuple({position: Fraction(1)} for position in range(length))


def _apply_stage(layer, stage, length):
    next_layer = []
    for output in range(length):
        combined = {}
        for offset, weight in stage:
            source = output + offset
            if 0 <= source < length:
                for original, coefficient in layer[source].items():
                    combined[original] = combined.get(original, Fraction(0)) + weight * coefficient
        next_layer.append({
            index: coefficient
            for index, coefficient in combined.items()
            if coefficient != 0
        })
    return next_layer


def compose_terms(length, stages):
    """Return, for every output position, the signed net coefficients of originals."""
    layer = _identity_layer(length)
    for stage in stages:
        layer = _apply_stage(layer, stage, length)
    return tuple(tuple(sorted(coefficients.items())) for coefficients in layer)


def build_cascade_plan(length, lo, hi, stages):
    if type(length) is not int or length <= 0:
        raise ValueError('invalid record length')
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= length:
        raise ValueError('invalid output window')
    frozen_stages = normalize_stages(stages)
    composed = compose_terms(length, frozen_stages)
    terms = composed[lo:hi]
    required = tuple(sorted({source for row in terms for source, _ in row}))
    return frozen_stages, terms, required


def evaluate_terms(index, terms, values):
    missing = []
    total = Fraction(0)
    for source, coefficient in terms:
        if source in values:
            total += coefficient * values[source]
        else:
            missing.append(source)
    if missing:
        return MissingRow(index, tuple(missing))
    return ValueRow(index, total)
