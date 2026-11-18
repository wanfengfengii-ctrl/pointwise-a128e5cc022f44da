import json
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from processing.result_rows import MissingRow, ValueRow
from source.import_wave import import_revision, import_wave_file
from source.read_samples import read_samples
from source.revisions import RevisionStore
from source.sample_transfer import decode_sample_transfer, encode_sample_transfer


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def expect_error(error, operation, *args):
    try:
        operation(*args)
    except error:
        return
    raise AssertionError(f"{operation.__name__} must raise {error.__name__}")


def run_cases():
    from exchange.prepare import prepare_exchange
    from processing.replay import run_exchange

    stages = ({0: 1, 1: 1}, {-1: -1, 0: 1})
    partial = (MissingRow(0, (1,)), ValueRow(1, Fraction(5)), MissingRow(2, (1,)))
    complete = tuple(ValueRow(i, Fraction(value)) for i, value in enumerate((5, 5, -3)))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        with closing(RevisionStore(root / "station.sqlite")) as revisions:
            revisions.initialize()
            old = import_wave_file(revisions, Path(__file__).with_name("examples") / "acquisition.json")
            cases = [
                ("center", old, stages, (1, 2), [0, 2], partial[1:2]),
                ("edge-and-gaps", old, stages, (0, 3), [0, 1, 2], partial),
                ("empty-window", old, stages, (1, 1), [], ()),
                ("empty-stage", old, (stages[0], {}, stages[1]), (1, 2), [], (ValueRow(1, Fraction(0)),)),
                ("rational", old, ({0: "1/3"}, {0: "5/2"}, {0: 1}), (2, 3), [2], (ValueRow(2, Fraction(35, 6)),)),
            ]
            pending = []

            def capture(case):
                label, revision, rules, window, indices, expected = case
                payload = prepare_exchange(revisions, revision, rules, *window)
                require(isinstance(payload, bytes), f"{label}: expected package bytes")
                header = json.loads(payload)
                require((header["version"], header["revision"], header["length"], header["window"],
                         header["required_samples"]) == (2, revision, 3, list(window), indices),
                        f"{label}: incorrect fixed package header or sample manifest")
                samples = read_samples(revisions, revision, header["required_samples"])
                package_path = root / f"{label}.window.json"
                sample_path = root / f"{label}.samples.json"
                package_path.write_bytes(payload)
                sample_path.write_bytes(encode_sample_transfer(samples))
                pending.append((label, revision, package_path, sample_path, expected))

            for case in cases:
                capture(case)
            expect_error(KeyError, prepare_exchange, revisions, "unknown", stages, 0, 1)
            expect_error(ValueError, prepare_exchange, revisions, old, ({0: 0}, stages[1]), 0, 1)
            expect_error(ValueError, prepare_exchange, revisions, old, stages, 0, 4)
            source = revisions.get_revision(old).source
            new = import_revision(revisions, source, 3, "mm/s", {1: 3}, old)
            capture(("complete-revision", new, stages, (0, 3), [0, 1, 2], complete))
            old_samples = encode_sample_transfer(read_samples(revisions, old, [0, 2]))
            require(old_samples == pending[0][3].read_bytes(), "correction changed original samples")

        for label, revision, package_path, sample_path, expected in pending:
            result = run_exchange(package_path.read_bytes(), revision, decode_sample_transfer(sample_path.read_bytes()))
            require(result == expected and all(not isinstance(row, ValueRow) or isinstance(row.value, Fraction)
                                               for row in result), f"{label}: unexpected rows {result!r}")
            print(json.dumps({"case": label, "required_samples": json.loads(package_path.read_bytes())["required_samples"],
                              "rows": [{"index": row.index, "value": str(row.value)} if isinstance(row, ValueRow)
                                       else {"index": row.index, "missing": list(row.missing)} for row in result]}))

        _, _, center_path, sample_path, _ = pending[0]
        center = center_path.read_bytes()
        samples = decode_sample_transfer(sample_path.read_bytes())
        foreign = decode_sample_transfer(pending[-1][3].read_bytes())
        expect_error(ValueError, run_exchange, center, old, foreign)
        expect_error(ValueError, run_exchange, center, new, samples)
        inconsistent = json.loads(center)
        inconsistent["required_samples"] = [0]
        expect_error(ValueError, run_exchange, json.dumps(inconsistent).encode("utf-8"), old, samples)


if __name__ == "__main__":
    run_cases()
