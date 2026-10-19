# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License

"""
Fail if a pytest JUnit report has failures or errors, or no tests.

QtWebEngine may crash when the test process exits, after the report is
written, so the report is more reliable than pytest's exit code.
"""

import sys
import xml.etree.ElementTree as ET


def main(path: str) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        print(f"No test report ({error}), so the tests didn't finish")
        return 1

    suites = [root] if root.tag == "testsuite" else list(root)
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
        for case in suite.iter("testcase"):
            for child in case:
                if child.tag in ("failure", "error"):
                    print(f"{child.tag.upper()}: {case.get('name')}")
                    print(f"  {(child.get('message') or '')[:500]}")

    print(", ".join(f"{value} {key}" for key, value in totals.items()))
    if totals["tests"] == 0:
        print("No tests ran")
        return 1
    return 1 if totals["failures"] or totals["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
