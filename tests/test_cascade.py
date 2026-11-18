import json
from fractions import Fraction

from exchange.cascade_codec import decode_cascade_package
from exchange.prepare import prepare_exchange
from processing.replay import run_exchange
from processing.result_rows import MissingRow, ValueRow
from source.import_wave import import_revision
from source.read_samples import read_samples
from source.sample_map import SparseSamples
from source.sample_transfer import decode_sample_transfer, encode_sample_transfer
from tests.test_exchange import StationCase


class CascadeTests(StationCase):
    def prepare(self, revision, stages, lo, hi):
        with self.station() as (revisions, _, _):
            payload = prepare_exchange(revisions, revision, stages, lo, hi)
            required = json.loads(payload)['required_samples']
            samples = read_samples(revisions, revision, required)
        return payload, encode_sample_transfer(samples)

    def test_hand_examples(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 3, 'mm/s', {0: 2, 2: 7})
            stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
            center = prepare_exchange(revisions, revision, stages, 1, 2)
            full = prepare_exchange(revisions, revision, stages, 0, 3)
            empty = prepare_exchange(revisions, revision, stages, 1, 1)
            center_samples = read_samples(revisions, revision, json.loads(center)['required_samples'])
            full_samples = read_samples(revisions, revision, json.loads(full)['required_samples'])
        self.assertEqual(json.loads(center)['required_samples'], [0, 2])
        self.assertEqual(run_exchange(center, revision, center_samples), (ValueRow(1, Fraction(5)),))
        self.assertEqual(run_exchange(full, revision, full_samples),
                         (MissingRow(0, (1,)), ValueRow(1, Fraction(5)), MissingRow(2, (1,))))
        self.assertEqual(json.loads(empty)['required_samples'], [])
        self.assertEqual(run_exchange(empty, revision, SparseSamples(revision, {})), ())

    def test_cross_stage_cancellation_drops_source(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 4, 'mm/s', {0: 2, 1: 3, 2: 5, 3: 7})
            # First stage spreads x_i+x_{i+1}; the second subtracts the delayed copy,
            # so for an interior output the original at i cancels to zero.
            stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
            payload = prepare_exchange(revisions, revision, stages, 1, 2)
            samples = read_samples(revisions, revision, json.loads(payload)['required_samples'])
        document = json.loads(payload)
        self.assertEqual(document['version'], 2)
        package = decode_cascade_package(payload)
        # Output 1 = -x_0 + x_2: original 1 cancels, so it is neither collected nor under-read.
        self.assertEqual(package.terms, (((0, Fraction(-1)), (2, Fraction(1))),))
        self.assertEqual(document['required_samples'], [0, 2])
        self.assertEqual(run_exchange(payload, revision, samples), (ValueRow(1, Fraction(3)),))
        # Even when 1 stays unrecorded and an extra sample for it is supplied, only source 2 is under-read.
        self.assertEqual(run_exchange(payload, revision, SparseSamples(revision, {0: 2, 1: 3})),
                         (MissingRow(1, (2,)),))

    def test_head_truncation_then_gap(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 3, 'mm/s', {0: 2, 2: 7})
            stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
            payload = prepare_exchange(revisions, revision, stages, 1, 3)
            samples = read_samples(revisions, revision, json.loads(payload)['required_samples'])
        self.assertEqual(json.loads(payload)['required_samples'], [0, 1, 2])
        self.assertEqual(run_exchange(payload, revision, samples),
                         (ValueRow(1, Fraction(5)), MissingRow(2, (1,))))

    def test_outside_record_reads_zero_at_every_stage(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 6})
            # Every output pulls only from outside the record, so exact zero needs no samples.
            payload = prepare_exchange(revisions, revision, ({2: 2, 3: -1}, {-5: 1}), 0, 2)
            samples = read_samples(revisions, revision, json.loads(payload)['required_samples'])
        self.assertEqual(json.loads(payload)['required_samples'], [])
        self.assertEqual(run_exchange(payload, revision, samples),
                         (ValueRow(0, Fraction(0)), ValueRow(1, Fraction(0))))

    def test_measured_zero_is_a_value(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 0, 1: 4})
            payload, transfer = self.prepare(revision, ({0: 1}, {0: 1}), 0, 2)
        result = run_exchange(payload, revision, decode_sample_transfer(transfer))
        self.assertEqual(result, (ValueRow(0, Fraction(0)), ValueRow(1, Fraction(4))))

    def test_extra_same_revision_samples_allowed(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 3, 'mm/s', {0: 2, 1: 3, 2: 7})
            payload = prepare_exchange(revisions, revision, ({0: 1}, {0: 1}), 1, 2)
        result = run_exchange(payload, revision, SparseSamples(revision, {0: 9, 1: 3, 2: 7}))
        self.assertEqual(result, (ValueRow(1, Fraction(3)),))

    def test_six_stages_allowed_and_rational_chain(self):
        stages = tuple({0: '1/2'} for _ in range(6))
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 1, 'mm/s', {0: 8})
            payload, transfer = self.prepare(revision, stages, 0, 1)
        result = run_exchange(payload, revision, decode_sample_transfer(transfer))
        self.assertEqual(result, (ValueRow(0, Fraction(1, 8)),))

    def test_stage_count_validation(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 1})
            for stages in ([], [{0: 1}], tuple({0: 1} for _ in range(7))):
                with self.subTest(count=len(stages)), self.assertRaises(ValueError):
                    prepare_exchange(revisions, revision, stages, 0, 1)
            with self.assertRaises(ValueError):
                prepare_exchange(revisions, revision, {0: 1}, 0, 1)

    def test_stage_entry_validation(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 1})
            invalid = (
                ({0: 0}, {0: 1}),
                ({0: 0.5}, {0: 1}),
                ({True: 1}, {0: 1}),
                ({0: 1, 1: '1/0'}),
                ({}, [{0: 1}]),
                ([(0, 1), (0, 2)], {0: 1}),
            )
            for stages in invalid:
                with self.subTest(stages=stages), self.assertRaises(ValueError):
                    prepare_exchange(revisions, revision, stages, 0, 1)

    def test_window_and_revision_validation(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 1})
            stages = ({0: 1}, {0: 1})
            for lo, hi in ((1, 0), (0, 3), (-1, 1), (False, 1)):
                with self.subTest(lo=lo, hi=hi), self.assertRaises(ValueError):
                    prepare_exchange(revisions, revision, stages, lo, hi)
            with self.assertRaises(KeyError):
                prepare_exchange(revisions, 'R-missing', stages, 0, 1)
            with self.assertRaises(KeyError):
                prepare_exchange(revisions, '', stages, 0, 1)

    def test_revision_binding_must_match_on_all_three_identifiers(self):
        with self.station() as (revisions, _, _):
            old = import_revision(revisions, 'table', 2, 'mm/s', {0: 2, 1: 3})
            new = import_revision(revisions, 'table', 2, 'mm/s', {0: 8}, old)
            payload = prepare_exchange(revisions, old, ({0: 1}, {0: 1}), 0, 2)
            correct = read_samples(revisions, old, [0, 1])
            foreign = read_samples(revisions, new, [0, 1])
        with self.assertRaises(ValueError):
            run_exchange(payload, new, correct)
        with self.assertRaises(ValueError):
            run_exchange(payload, old, foreign)
        with self.assertRaises(ValueError):
            run_exchange(payload, 'R-missing', correct)

    def test_invalid_sample_index_rejected(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 2, 'mm/s', {0: 2})
            payload = prepare_exchange(revisions, revision, ({0: 1}, {0: 1}), 0, 1)
        for index in (-1, 2, True, '0'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                run_exchange(payload, revision, SparseSamples(revision, {index: 1}))

    def test_tampered_package_rejected(self):
        with self.station() as (revisions, _, _):
            revision = import_revision(revisions, 'table', 3, 'mm/s', {0: 2, 2: 7})
            payload = prepare_exchange(revisions, revision, ({0: 1, 1: 1}, {-1: -1, 0: 1}), 0, 3)
            samples = read_samples(revisions, revision, [0, 1, 2])
        document = json.loads(payload)
        tampered = (
            {'version': 1},
            {'version': 3},
            {'required_samples': [0, 2]},
            {'required_samples': [2, 0, 1]},
            {'required_samples': [True, 1, 2]},
            {'window': [0, 2]},
            {'window': [1, 3]},
            {'length': 4},
            {'plan': {'stages': [[[0, 1, 1]]], 'terms': document['plan']['terms']}},
            {'plan': {'stages': document['plan']['stages'],
                      'terms': [[[0, 9, 1]], [[1, 5, 1]], [[2, 0, 1]]]}},
            {'revision': 'R-other'},
        )
        for change in tampered:
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_exchange(json.dumps(document | change).encode('utf-8'), revision, samples)
        with self.assertRaises(ValueError):
            run_exchange(b'{', revision, samples)
        with self.assertRaises(ValueError):
            run_exchange(json.dumps([document]).encode(), revision, samples)

    def test_correction_keeps_original_binding_and_recomputes_identically(self):
        with self.station() as (revisions, _, _):
            old = import_revision(revisions, 'table', 3, 'mm/s', {0: 2, 2: 7})
            stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
            old_payload = prepare_exchange(revisions, old, stages, 0, 3)
            old_samples = encode_sample_transfer(read_samples(revisions, old, [0, 1, 2]))
            source = revisions.get_revision(old).source
            new = import_revision(revisions, source, 3, 'mm/s', {1: 3}, old)
            new_payload = prepare_exchange(revisions, new, stages, 0, 3)
            new_samples = encode_sample_transfer(read_samples(revisions, new, [0, 1, 2]))
        self.assertEqual(run_exchange(old_payload, old, decode_sample_transfer(old_samples)),
                         (MissingRow(0, (1,)), ValueRow(1, Fraction(5)), MissingRow(2, (1,))))
        self.assertEqual(run_exchange(new_payload, new, decode_sample_transfer(new_samples)),
                         tuple(ValueRow(i, Fraction(value)) for i, value in enumerate((5, 5, -3))))
        # The older package with the older samples still replays to the same answer.
        self.assertEqual(run_exchange(old_payload, old, decode_sample_transfer(old_samples)),
                         (MissingRow(0, (1,)), ValueRow(1, Fraction(5)), MissingRow(2, (1,))))
        with self.assertRaises(ValueError):
            run_exchange(old_payload, new, decode_sample_transfer(new_samples))
