"""pytest なしでもテストを走らせる最小ランナー: ``python tests/test_xxx.py``。"""

from __future__ import annotations

import sys
import traceback


def run_module_tests(namespace: dict) -> None:
    tests = [(name, fn) for name, fn in namespace.items() if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
