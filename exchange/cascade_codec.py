import json
from dataclasses import dataclass
from fractions import Fraction

from exchange.window import Window
from processing.cascade import build_cascade_plan
from source.sample_map import parse_rational_parts


CASCADE_FORMAT_V2 = 2
_CASCADE_FIELDS = {'version', 'revision', 'length', 'window', 'required_samples', 'plan'}
_PLAN_FIELDS = {'stages', 'terms'}


@dataclass(frozen=True)
class CascadePackage:
    revision: str
    length: int
    window: Window
    stages: tuple[tuple[tuple[int, Fraction], ...], ...]
    terms: tuple[tuple[tuple[int, Fraction], ...], ...]
    required_samples: tuple[int, ...]


def _encode_taps(stage):
    return [[offset, weight.numerator, weight.denominator] for offset, weight in stage]


def _decode_taps(entries):
    if not isinstance(entries, list):
        raise ValueError('invalid stage taps')
    taps = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('invalid frozen stage tap')
        offset, numerator, denominator = entry
        taps.append((offset, parse_rational_parts(numerator, denominator)))
    return tuple(taps)


def _decode_terms(entries):
    if not isinstance(entries, list):
        raise ValueError('invalid output terms')
    rows = []
    for row in entries:
        if not isinstance(row, list):
            raise ValueError('invalid output coefficient row')
        terms = []
        for entry in row:
            if not isinstance(entry, list) or len(entry) != 3:
                raise ValueError('invalid frozen coefficient')
            source, numerator, denominator = entry
            terms.append((source, parse_rational_parts(numerator, denominator)))
        rows.append(tuple(terms))
    return tuple(rows)


def validate_cascade_package(package):
    if not isinstance(package, CascadePackage):
        raise ValueError('expected a V2 cascade package')
    if not isinstance(package.revision, str) or not package.revision.strip():
        raise ValueError('invalid cascade revision identifier')
    if type(package.length) is not int or package.length <= 0:
        raise ValueError('invalid record length')
    window = package.window
    if (not isinstance(window, Window) or type(window.lo) is not int or type(window.hi) is not int
            or not 0 <= window.lo <= window.hi <= package.length):
        raise ValueError('invalid cascade window')
    if not isinstance(package.stages, tuple) or not package.stages:
        raise ValueError('invalid frozen cascade stages')
    if not isinstance(package.terms, tuple) or len(package.terms) != window.hi - window.lo:
        raise ValueError('terms must cover the entire window')
    required = package.required_samples
    if not isinstance(required, tuple) or any(type(index) is not int for index in required):
        raise ValueError('invalid cascade sample manifest')
    # Recompose everything from the frozen stages and require the package to agree
    # with it, so the execution representation, terms and manifest are self-contained.
    stages, terms, samples = build_cascade_plan(package.length, window.lo, window.hi, package.stages)
    if stages != package.stages or terms != package.terms or samples != required:
        raise ValueError('cascade package is not self-consistent')
    return package


def encode_cascade_package(package):
    validate_cascade_package(package)
    document = {
        'version': CASCADE_FORMAT_V2,
        'revision': package.revision,
        'length': package.length,
        'window': [package.window.lo, package.window.hi],
        'required_samples': list(package.required_samples),
        'plan': {
            'stages': [_encode_taps(stage) for stage in package.stages],
            'terms': [[[source, coefficient.numerator, coefficient.denominator]
                       for source, coefficient in row] for row in package.terms],
        },
    }
    return json.dumps(document, sort_keys=True, separators=(',', ':')).encode('utf-8')


def decode_cascade_package(payload):
    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid cascade package JSON') from exc
    if not isinstance(document, dict) or document.keys() != _CASCADE_FIELDS:
        raise ValueError('invalid cascade package fields')
    if type(document['version']) is not int or document['version'] != CASCADE_FORMAT_V2:
        raise ValueError('unsupported cascade package version')
    revision = document['revision']
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError('invalid cascade revision identifier')
    length = document['length']
    if type(length) is not int or length <= 0:
        raise ValueError('invalid record length')
    bounds = document['window']
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError('invalid cascade window')
    lo, hi = bounds
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= length:
        raise ValueError('invalid cascade window')
    manifest = document['required_samples']
    if not isinstance(manifest, list) or any(type(index) is not int for index in manifest):
        raise ValueError('invalid cascade sample manifest')
    plan = document['plan']
    if not isinstance(plan, dict) or plan.keys() != _PLAN_FIELDS:
        raise ValueError('invalid cascade plan fields')
    stages = tuple(_decode_taps(stage) for stage in plan['stages'])
    terms = _decode_terms(plan['terms'])
    package = CascadePackage(revision, length, Window(lo, hi), stages, terms, tuple(manifest))
    return validate_cascade_package(package)
