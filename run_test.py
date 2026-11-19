"""Run one unittest module without racing OCR warmup against Python shutdown."""

import faulthandler
import os
import unittest
from unittest.mock import patch


def translate_for_test(task, message):
    # Isolated fixtures and TaskTestCase construct tasks without an app.
    app = getattr(task, '_app', None)
    return app.tr(message) if app is not None else message


def _patch_mon_test_compat():
    """Compatibility shim for Mon-script's TaskTestCase.

    Mon.test.TaskTestCase imports `from Mon.test import Mon` in setUpClass /
    set_image / set_images, but Mon.test.__init__ only exports a lowercase
    `ok` object. The imported name is never actually used (the code uses the
    module-global `ok` instead), so the stale import is harmless once the
    symbol exists. Inject an alias so those tests run on this Mon-script build.
    """
    import Mon.test
    if not hasattr(Mon.test, 'Mon'):
        Mon.test.Mon = getattr(Mon.test, 'ok', None) or object()


def main():
    # Windows CI hosts can advertise AMX instructions that the VM cannot execute.
    # Keep real OCR inference on a portable oneDNN path, before OpenVINO loads.
    os.environ.setdefault('ONEDNN_MAX_CPU_ISA', 'AVX2')
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)
    _patch_mon_test_compat()
    # TaskExecutor.ocr_lib still loads real models synchronously when a test
    # needs OCR. Template-only tests never start the native model loader.
    with patch('Mon.task.TaskExecutor.TaskExecutor.init_default_ocr', lambda self: None), \
            patch('Mon.task.task.BaseTask.tr', translate_for_test):
        unittest.main(module=None, verbosity=2)


if __name__ == '__main__':
    main()
