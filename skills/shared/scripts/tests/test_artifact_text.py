import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artifact_text import bundle, revise, render


class ArtifactTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = '约束\r\n行为\r\n边界\r\n'.encode()
        (self.root / 'source.md').write_bytes(self.raw)
        self.item = {'path': 'source.md', 'sha256': hashlib.sha256(self.raw).hexdigest()}

    def test_render_requires_exact_fields_and_preserves_judgment(self):
        raw = b'# Report\n{{judgment}}\n{{evidence}}\n'
        (self.root / 'template.md').write_bytes(raw)
        manifest = {'template': 'template.md', 'sha256': hashlib.sha256(raw).hexdigest(),
                    'output': 'report.md', 'fields': {'judgment': '独立结论'}}
        with self.assertRaises(ValueError):
            render(self.root, manifest)
        manifest['fields']['evidence'] = 'candidate/path#DoD-1'
        render(self.root, manifest)
        self.assertEqual((self.root / 'report.md').read_text(), '# Report\n独立结论\ncandidate/path#DoD-1\n')

    def test_overlap_preserves_exact_source(self):
        result = bundle(self.root, {'sources': [dict(self.item, start=1, end=2), dict(self.item, start=2, end=3)]})
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['text'].encode(), self.raw)

    def test_stale_and_out_of_bounds_fail(self):
        for item in [dict(self.item, sha256='0' * 64), dict(self.item, end=4)]:
            with self.assertRaises(ValueError):
                bundle(self.root, {'sources': [item]})

    def test_revision_preserves_source_and_untouched_bytes(self):
        manifest = {'source': 'source.md', 'sha256': self.item['sha256'], 'output': 'new.md',
                    'replacements': [{'old': '行为', 'new': '新行为'}]}
        revise(self.root, manifest)
        self.assertEqual((self.root / 'source.md').read_bytes(), self.raw)
        self.assertEqual((self.root / 'new.md').read_bytes(), self.raw.replace('行为'.encode(), '新行为'.encode()))
        with self.assertRaises(FileExistsError):
            revise(self.root, manifest)

    def test_ambiguous_and_overlapping_edits_do_not_create_output(self):
        for edits in [[{'old': '\r\n', 'new': ''}],
                      [{'old': '约束', 'new': ''}, {'old': '约束\r\n行为', 'new': ''}]]:
            with self.assertRaises(ValueError):
                revise(self.root, {'source': 'source.md', 'sha256': self.item['sha256'],
                                   'output': 'new.md', 'replacements': edits})
            self.assertFalse((self.root / 'new.md').exists())

if __name__ == '__main__':
    unittest.main()
