import unittest
from pathlib import Path

from cascade_checks import run_cases


def main():
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), top_level_dir=str(root))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    run_cases()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
