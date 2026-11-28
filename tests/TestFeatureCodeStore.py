"""特征码与账号关联：提取、学习、反查。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.task.account.FeatureCodeStore import (
    FEATURE_CODE_RE, FeatureCodeStore, extract_feature_code,
)


class TestExtract(unittest.TestCase):

    def test_extracts_from_labeled_text(self):
        self.assertEqual('109506193', extract_feature_code('特征码: 109506193'))
        self.assertEqual('109506193', extract_feature_code('特征码：109506193'))
        self.assertEqual('109506193', extract_feature_code('特征码 109506193'))

    def test_ignores_unrelated_text(self):
        self.assertIsNone(extract_feature_code('登录'))
        self.assertIsNone(extract_feature_code(''))
        self.assertIsNone(extract_feature_code(None))
        self.assertIsNone(extract_feature_code('abc123'))  # 纯字母数字无"特征码"前缀

    def test_accepts_digit_ranges(self):
        self.assertEqual('123456', extract_feature_code('特征码: 123456'))
        self.assertEqual('123456789012', extract_feature_code('特征码: 123456789012'))


class TestStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FeatureCodeStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_learn_and_lookup(self):
        self.assertTrue(self.store.learn('1317250', '109506193'))
        self.assertEqual('1317250', self.store.account_for('109506193'))
        self.assertIsNone(self.store.account_for('999999999'))

    def test_learn_same_mapping_is_noop(self):
        self.store.learn('1317250', '109506193')
        self.assertFalse(self.store.learn('1317250', '109506193'))

    def test_learn_remap(self):
        self.store.learn('old-account', '109506193')
        self.store.learn('new-account', '109506193')
        self.assertEqual('new-account', self.store.account_for('109506193'))

    def test_invalid_inputs_ignored(self):
        self.assertFalse(self.store.learn('', '109506193'))
        self.assertFalse(self.store.learn('acc', ''))
        self.assertFalse(self.store.learn('acc', 'abc'))
        self.assertIsNone(self.store.account_for(''))

    def test_survives_reload(self):
        self.store.learn('1317250', '109506193')
        fresh = FeatureCodeStore(self.tmp.name)
        self.assertEqual('1317250', fresh.account_for('109506193'))

    def test_corrupt_file_returns_empty(self):
        Path(self.tmp.name, 'FeatureCodes.json').write_text('{broken', encoding='utf-8')
        self.assertEqual({}, self.store.load())
        self.assertIsNone(self.store.account_for('109506193'))

    def test_does_not_touch_account_config(self):
        """特征码独立存储，不能写进 MultiAccountConfig.json（会被 GUI 覆盖字段）。"""
        self.store.learn('acc', '109506193')
        files = list(Path(self.tmp.name).iterdir())
        self.assertIn('FeatureCodes.json', [f.name for f in files])
        self.assertNotIn('MultiAccountConfig.json', [f.name for f in files])


if __name__ == '__main__':
    unittest.main()
