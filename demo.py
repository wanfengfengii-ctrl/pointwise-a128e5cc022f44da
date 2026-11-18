import json
from contextlib import ExitStack, closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from exchange.issue_store import WindowIssueStore, export_window
from exchange.prepare import prepare_exchange, prepare_window
from ledger.admission import publish_result
from ledger.export import export_result
from ledger.report_reader import read_result
from ledger.report_store import ResultStore
from processing.replay import run_exchange, run_window
from processing.result_rows import MissingRow, ValueRow
from recipe.catalog import register_recipe
from recipe.revision_store import RecipeStore
from source.import_wave import import_revision, import_wave_file
from source.read_samples import read_samples
from source.revisions import RevisionStore
from source.sample_transfer import decode_sample_transfer, encode_sample_transfer


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def stage_window(directory, label, revisions, recipes, issues, revision, version):
    package = prepare_window(revisions, recipes, issues, revision, version, 0, 3)
    samples = read_samples(revisions, package.revision, package.required_samples)
    window_file = directory / f'{label}.window.json'
    sample_file = directory / f'{label}.samples.json'
    window_file.write_bytes(export_window(issues, package.issue_id))
    sample_file.write_bytes(encode_sample_transfer(samples))
    check(package.required_samples == (0, 1, 2), 'unexpected sample manifest')
    return window_file, sample_file


def review_and_archive(ledger_path, result_id, transfer):
    window_file, sample_file = transfer
    payload = window_file.read_bytes()
    result = run_window(payload, decode_sample_transfer(sample_file.read_bytes()))
    with closing(ResultStore(ledger_path)) as ledger:
        ledger.initialize()
        archived = publish_result(ledger, result_id, payload, result)
    return archived


def cascade_handover(directory, station_path, examples):
    """Demonstrate the V2 multi-stage exchange: collect on the manifest, then run offline."""
    stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
    with closing(RevisionStore(station_path)) as revisions:
        revisions.initialize()
        original = import_wave_file(revisions, examples / 'acquisition.json')

        def handoff(label, lo, hi):
            payload = prepare_exchange(revisions, original, stages, lo, hi)
            document = json.loads(payload)
            samples = read_samples(revisions, original, document['required_samples'])
            package_path = directory / f'{label}.exchange.json'
            samples_path = directory / f'{label}.exchange-samples.json'
            package_path.write_bytes(payload)
            samples_path.write_bytes(encode_sample_transfer(samples))
            return package_path, samples_path, document

        transfers = [
            handoff('head-cancel', 1, 2),
            handoff('head-truncated-gap', 0, 3),
            handoff('empty-window', 1, 1),
        ]
        # The correction below publishes a new revision while the original binding stays fixed.
        base = revisions.get_revision(original)
        corrected = import_revision(revisions, base.source, base.length, base.unit, {1: 3}, original)
        complete_path = directory / 'complete.exchange.json'
        complete_payload = prepare_exchange(revisions, corrected, stages, 0, 3)
        complete_path.write_bytes(complete_payload)
        complete_samples = read_samples(revisions, corrected, [0, 1, 2])
        complete_sample_path = directory / 'complete.exchange-samples.json'
        complete_sample_path.write_bytes(encode_sample_transfer(complete_samples))

    # The station library is closed here. Only the self-contained package bytes and the
    # same-revision sparse sample files are used from now on; no store is ever reopened.
    for label, (package_path, samples_path, document) in zip(
            ('head-cancel', 'head-truncated-gap', 'empty-window'), transfers):
        rows = run_exchange(package_path.read_bytes(), original,
                            decode_sample_transfer(samples_path.read_bytes()))
        check(document['version'] == 2, 'exchange package must be version 2')
        serialized = [
            {'index': row.index, 'value': str(row.value)} if isinstance(row, ValueRow)
            else {'index': row.index, 'missing': list(row.missing)} for row in rows
        ]
        print(json.dumps({'case': label, 'revision': document['revision'],
                          'window': document['window'], 'required_samples': document['required_samples'],
                          'rows': serialized}, ensure_ascii=False, sort_keys=True))

    # Cross-stage cancellation: interior output 1 collapses to -x_0 + x_2, so original
    # index 1 never enters the manifest even though both stages read intermediate index 1.
    package_path, samples_path, document = transfers[0]
    check(document['required_samples'] == [0, 2], 'canceled index must leave the collection manifest')
    check(run_exchange(package_path.read_bytes(), original,
                       decode_sample_transfer(samples_path.read_bytes())) == (ValueRow(1, Fraction(5)),),
          'cross-stage cancellation result mismatch')

    # Head truncation followed by a gap: the full window keeps the boundary zeros and
    # reports the unrecorded original 1 for both affected outputs.
    package_path, _, document = transfers[1]
    check(document['required_samples'] == [0, 1, 2], 'gap must stay on the collection manifest')
    partial = decode_sample_transfer((directory / 'head-truncated-gap.exchange-samples.json').read_bytes())
    check(run_exchange(package_path.read_bytes(), original, partial)
          == (MissingRow(0, (1,)), ValueRow(1, Fraction(5)), MissingRow(2, (1,))),
          'head-truncated gap processing mismatch')

    # The new revision replays to its own values; the original package above is unchanged.
    complete_rows = run_exchange(complete_path.read_bytes(), corrected,
                                 decode_sample_transfer(complete_sample_path.read_bytes()))
    check(complete_rows == tuple(ValueRow(i, Fraction(value)) for i, value in enumerate((5, 5, -3))),
          'corrected multi-stage replay mismatch')
    return original, corrected


def main():
    examples = Path(__file__).resolve().parent / 'examples'
    definition = json.loads((examples / 'neighbor_mean.json').read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        directory = Path(directory)
        station_path = directory / 'station.sqlite'
        ledger_path = directory / 'review.sqlite'
        with ExitStack() as stack:
            revisions, recipes, issues = [stack.enter_context(closing(kind(station_path)))
                                          for kind in (RevisionStore, RecipeStore, WindowIssueStore)]
            for store in (revisions, recipes, issues):
                store.initialize()
            original = import_wave_file(revisions, examples / 'acquisition.json')
            version = register_recipe(recipes, **definition)
            first = stage_window(directory, 'first', revisions, recipes, issues, original, version)
        first_archive = review_and_archive(ledger_path, 'A1', first)
        with ExitStack() as stack:
            revisions, recipes, issues = [stack.enter_context(closing(kind(station_path)))
                                          for kind in (RevisionStore, RecipeStore, WindowIssueStore)]
            for store in (revisions, recipes, issues):
                store.initialize()
            base = revisions.get_revision(original)
            corrected = import_revision(revisions, base.source, base.length, base.unit, {1: 3, 2: 9}, original)
            check(revisions.current_revision(base.source) == corrected, 'correction not published')
            check(dict(read_samples(revisions, original, (0, 1, 2)).samples) == {0: 2, 2: 7},
                  'original revision changed')
            second = stage_window(directory, 'second', revisions, recipes, issues, corrected, version)
        second_archive = review_and_archive(ledger_path, 'A2', second)
        with closing(ResultStore(ledger_path)) as ledger:
            reopened = [read_result(ledger, result_id) for result_id in ('A1', 'A2')]
            check(reopened == [first_archive, second_archive], 'reopened archives changed')
            check([record.package.revision for record in reopened] == [original, corrected], 'archive revision mismatch')
            check(reopened[0].rows == (MissingRow(0, (1,)), MissingRow(1, (1,)), ValueRow(2, Fraction(7, 2))),
                  'incorrect original result')
            check(reopened[1].rows == (ValueRow(0, Fraction(5, 2)), ValueRow(1, Fraction(6)), ValueRow(2, Fraction(9, 2))),
                  'incorrect corrected result')
            for record in reopened:
                document = json.loads(export_result(ledger, record.result_id))
                summary = {'result_id': document['result_id'], 'unit': document['package']['output_unit'],
                           'required_samples': document['package']['required_samples'], 'rows': document['rows']}
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        # Multi-stage exchange: collection while connected, then fully offline replay
        # with neither the station library nor the ledger available.
        cascade_handover(directory, station_path, examples)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
