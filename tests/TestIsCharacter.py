"""P2 is_character：按 char_dict 注册身份匹配，不受自定义轮转继承影响。"""
import unittest
from unittest.mock import Mock
from src.char.BaseChar import BaseChar
from src.char.ShoreKeeper import ShoreKeeper
from src.char.CharFactory import char_dict


def make_custom_shorekeeper():
    """模拟工坊自定义角色：类名继承 ShoreKeeper，但 char_name 指向别的注册角色。"""
    class CustomRotation(ShoreKeeper):
        pass
    obj = CustomRotation.__new__(CustomRotation)
    obj.task = Mock()
    return obj


class TestIsCharacter(unittest.TestCase):

    def test_builtin_char_matches_own_class(self):
        # 找一个注册到 char_dict 的 ShoreKeeper 实例
        info = next((v for k, v in char_dict.items()
                     if getattr(v.get('cls'), '__name__', None) == 'ShoreKeeper'), None)
        self.assertIsNotNone(info)
        obj = info['cls'].__new__(info['cls'])
        obj.task = Mock()
        obj.char_name = info['canonical_name']
        self.assertTrue(obj.is_character(ShoreKeeper))
        self.assertFalse(obj.is_character(Mock))  # 不匹配其他类

    def test_custom_rotation_with_builtin_name_matches(self):
        # 自定义轮转继承 ShoreKeeper 且 char_name 是 ShoreKeeper -> 应匹配
        obj = make_custom_shorekeeper()
        info = next((v for k, v in char_dict.items()
                     if getattr(v.get('cls'), '__name__', None) == 'ShoreKeeper'), None)
        obj.char_name = info['canonical_name']
        self.assertTrue(obj.is_character(ShoreKeeper))

    def test_no_char_name_falls_back_to_isinstance(self):
        obj = make_custom_shorekeeper()
        obj.char_name = None
        self.assertTrue(obj.is_character(ShoreKeeper))
        self.assertFalse(obj.is_character(Mock))

    def test_unknown_name_returns_false(self):
        # char_name 存在但不在 char_dict：身份未知，按 xjtu 语义返回 False
        obj = make_custom_shorekeeper()
        obj.char_name = 'not_registered_custom'
        self.assertFalse(obj.is_character(ShoreKeeper))

    def test_tuple_classes(self):
        from src.char.Verina import Verina
        obj = make_custom_shorekeeper()
        obj.char_name = None
        self.assertTrue(obj.is_character((Verina, ShoreKeeper)))


if __name__ == '__main__':
    unittest.main()
