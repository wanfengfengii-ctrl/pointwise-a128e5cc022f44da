from exchange.cascade_codec import CascadePackage, decode_cascade_package
from exchange.codec import decode_window
from exchange.manifest import WindowPackage, validate_package_manifest
from processing.cascade import evaluate_terms
from processing.convolution import evaluate_single_point
from processing.input_binding import bind_samples
from processing.result_rows import assemble_rows
from source.sample_map import SparseSamples, validate_sample_index


def run_window(package, samples):
    if isinstance(package, WindowPackage):
        validate_package_manifest(package)
    else:
        package = decode_window(package)
    values = bind_samples(package, samples)
    return assemble_rows(package, (evaluate_single_point(package, index, values) for index in package.window.indices()))


def run_exchange(package, revision, samples):
    """Replay a V2 multi-stage package offline, returning rows in output order."""
    if isinstance(package, CascadePackage):
        cascade = package
    elif isinstance(package, (bytes, bytearray)):
        cascade = decode_cascade_package(package)
    else:
        raise ValueError('invalid cascade package')
    if not isinstance(revision, str) or not revision.strip() or revision != cascade.revision:
        raise ValueError('exchange revision does not match the package binding')
    if not isinstance(samples, SparseSamples) or samples.revision != cascade.revision:
        raise ValueError('sample revision does not match the exchange package')
    for index in samples.samples:
        validate_sample_index(index, cascade.length)
    values = samples.samples
    rows = tuple(
        evaluate_terms(index, terms, values)
        for index, terms in zip(cascade.window.indices(), cascade.terms)
    )
    return rows
