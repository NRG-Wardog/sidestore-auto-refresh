"""Execute the complete immutable semantic suite on its original Linux platform.

Both active provenance workflows run this through test_*.py discovery in a
mandatory Ubuntu job. The macOS jobs retain native converter tests and the
platform-independent source-contract and fixture-failure tests.
"""
import unittest

import historical_semantic


class HistoricalSemanticExecutionTests(unittest.TestCase):
    def test_exact_frozen_semantic_suite(self):
        historical_semantic.run_tests()


if __name__ == '__main__':
    unittest.main()
