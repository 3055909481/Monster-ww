"""游戏内特征码与账号的关联存储。

鸣潮客户端右下角显示一个"特征码: 123456789"（账号唯一标识）。登录后自动
记录该特征码对应的账号 key，之后在游戏内读特征码即可反查当前账号，无需
切到登录界面查看。

数据单独存 configs/FeatureCodes.json，不写进 MultiAccountConfig.json：
账号编辑弹窗会重建后者的账号字典，新字段会被抹掉。本文件只由本模块维护。
"""

import json
import os
import re
import tempfile
from pathlib import Path

# "特征码: 109506193" / "特征码 109506193" / 纯数字行
FEATURE_CODE_RE = re.compile(r'特征码?\s*[:：]?\s*(\d{6,15})')


def extract_feature_code(text):
    """从 OCR 文本里提取特征码；找不到返回 None。"""
    if not text:
        return None
    match = FEATURE_CODE_RE.search(str(text))
    return match.group(1) if match else None


class FeatureCodeStore:
    def __init__(self, folder='configs'):
        self.path = Path(folder) / 'FeatureCodes.json'

    def load(self):
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding='utf-8') as stream:
                document = json.load(stream)
        except (OSError, ValueError):
            return {}
        # 结构：{"codes": {"109506193": "account_key"}}
        codes = document.get('codes') if isinstance(document, dict) else None
        if not isinstance(codes, dict):
            return {}
        return {str(code): str(key) for code, key in codes.items()
                if str(code).isdigit() and isinstance(key, str) and key}

    def save(self, codes):
        payload = {'codes': {str(c): str(k) for c, k in codes.items()}}
        self._write_json(self.path, payload)

    def learn(self, account_key, feature_code):
        """记录 特征码 -> 账号 的映射。返回是否发生变化。"""
        code = str(feature_code) if feature_code else None
        if not code or not code.isdigit() or not account_key:
            return False
        codes = self.load()
        if codes.get(code) == account_key:
            return False
        codes[code] = account_key
        self.save(codes)
        return True

    def account_for(self, feature_code):
        """按特征码反查账号 key；未记录返回 None。"""
        code = str(feature_code) if feature_code else None
        return self.load().get(code) if code else None

    @staticmethod
    def _write_json(path, document):
        content = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.FeatureCodes-', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
