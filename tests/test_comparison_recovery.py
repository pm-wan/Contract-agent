from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
import unittest
from contract_app.documents.models import ParsedDocument
from contract_app.documents.artifacts import parsed_document_sha256
from contract_app.rules.obligations import OBLIGATION_CATEGORY_ORDER
from contract_app.workflow.upstream_comparison import UpstreamComparisonProcessor

class ComparisonRecoveryTests(unittest.TestCase):
    def test_valid_category_survives_invalid_quote_in_another_category(self):
        text = '双方约定具体交付期限。'
        digest = sha256(text.encode()).hexdigest()
        parsed = ParsedDocument(digest, 'application/pdf', 'test-parser-v1', (text,), (), (), digest)
        def source():
            return SimpleNamespace(document_version_id=uuid4(), source_sha256=digest, parsed_sha256=parsed_document_sha256(parsed))
        up, down = source(), source()
        def category(key, quote):
            def evidence(side, src):
                return dict(side=side, kind='document_span', document_version_id=str(src.document_version_id), page=1, quote=quote)
            return dict(category=key.value, state='candidate', applicability='applicable', coverage='covered', upstream_evidence=[evidence('upstream', up)], procurement_evidence=[evidence('procurement', down)], limitations=[])
        valid = category(OBLIGATION_CATEGORY_ORDER[0], text)
        invalid = category(OBLIGATION_CATEGORY_ORDER[1], '虚构的引文')
        processor = object.__new__(UpstreamComparisonProcessor)
        accepted, failures = processor._validate_pair_categories(dict(categories=[valid, invalid]), up, down, parsed, parsed)
        self.assertEqual([item['category'] for item in accepted], [OBLIGATION_CATEGORY_ORDER[0].value])
        self.assertEqual(len(accepted[0]['evidence']), 2)
        self.assertTrue(any('obligation_evidence_quote_not_unique' in item for item in failures))
        duplicate = deepcopy(valid)
        accepted, failures = processor._validate_pair_categories(dict(categories=[valid, duplicate]), up, down, parsed, parsed)
        self.assertFalse(accepted)
        self.assertTrue(any('missing_or_duplicate_category' in item for item in failures))

    def test_fenced_json_is_supported_but_arbitrary_text_is_rejected(self):
        self.assertEqual(UpstreamComparisonProcessor._decode_comparison_output('```json\n{"categories": []}\n```'), {'categories': []})
        with self.assertRaises(ValueError):
            UpstreamComparisonProcessor._decode_comparison_output('not JSON')

if __name__ == '__main__':
    unittest.main()
