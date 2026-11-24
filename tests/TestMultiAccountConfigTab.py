"""Layout regression for the multi-account config cards."""

import unittest

from PySide6.QtWidgets import QApplication, QSizePolicy

from src.gui.MultiAccountConfigTab import AccountTaskCard


class AccountTaskCardLayoutTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_header_never_stretches_and_card_is_not_height_locked(self):
        card = AccountTaskCard({'account_key': '', 'task': None, 'config': {}}, [], lambda _: None)

        self.assertEqual(QSizePolicy.Fixed, card.header.sizePolicy().verticalPolicy())
        # The old bug froze the card at a stale sizeHint sampled during the
        # expand/collapse transition, which stretched the header across a tall
        # empty surface. A frozen height is fine as long as it tracks content.
        card.set_expanded(False)
        self.assertLess(card.sizeHint().height(), 120)
        card.resize_content()
        self.assertLess(card.height(), 120)


if __name__ == '__main__':
    unittest.main()
