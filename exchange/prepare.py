from uuid import uuid4

from exchange.cascade_codec import CascadePackage, encode_cascade_package
from exchange.manifest import WindowPackage, build_sample_manifest
from exchange.window import validate_window
from processing.cascade import build_cascade_plan
from recipe.units import require_input_unit


def prepare_window(revisions, recipes, issues, revision, recipe_version, lo, hi):
    record = revisions.get_revision(revision)
    recipe = recipes.get_version(recipe_version)
    window = validate_window(record.length, lo, hi)
    require_input_unit(record.unit, recipe.units)
    required = build_sample_manifest(record.length, window, recipe.taps)
    package = WindowPackage(
        f'E-{uuid4().hex}', record.revision, record.length, recipe.version,
        recipe.units, window, recipe.taps, required,
    )
    issues.save(package)
    return package


def prepare_exchange(revisions, revision, stages, lo, hi):
    """Build a self-contained V2 multi-stage exchange package without archiving."""
    record = revisions.get_revision(revision)
    frozen_stages, terms, required = build_cascade_plan(record.length, lo, hi, stages)
    package = CascadePackage(record.revision, record.length, validate_window(record.length, lo, hi),
                             frozen_stages, terms, required)
    return encode_cascade_package(package)
