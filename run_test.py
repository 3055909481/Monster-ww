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

    Mon-script's Mon.test.TaskTestCase uses a module-global `ok` in four
    places (setUpClass / set_image / set_images) but never imports it -- the
    real object lives in Mon.test.ok and is created by init_ok(). It also does
    `from Mon.test import Mon`, which Mon.test does not export. Both are
    upstream packaging bugs. Provide a lazy proxy that forwards attribute
    access to Mon.test.ok, so the tests work on this Mon-script build without
    patching the installed package or dropping any test.
    """
    import Mon.test

    class _OkProxy:
        def __getattr__(self, name):
            target = getattr(Mon.test, 'ok', None)
            if target is None:
                raise RuntimeError('Mon.test.ok is not initialized (init_ok not called)')
            return getattr(target, name)

    if not hasattr(Mon.test, 'Mon'):
        Mon.test.Mon = _OkProxy()
    try:
        import Mon.test.TaskTestCase as task_test_case
        if not hasattr(task_test_case, 'ok'):
            task_test_case.ok = _OkProxy()
    except ImportError:
        pass


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
