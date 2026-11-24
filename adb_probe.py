"""Pure helpers for the ADB emulator validation task.

Kept at the repository root (outside ``src/``) so the logic can be unit tested
without importing Cython-compiled task modules.
"""

import time

ADB_PACKAGES = ('com.kurogame.mingchao', 'com.kurogame.wutheringwaves')


def adb_config():
    """Return the minimal app config that enables ADB device discovery."""
    return {
        'packages': list(ADB_PACKAGES),
        'capture_method': 'adb',
    }


def sample_capture(capture, count=5, busy_wait=1.0, clock=time.monotonic):
    """Sample consecutive frames from a capture backend.

    Returns frame count, last resolution, observed FPS and the first few
    errors. Never raises on a single failed frame so the task can report a
    useful diagnosis instead of dying on the first timeout.
    """
    if count < 1:
        raise ValueError('count must be at least 1')
    frames = 0
    durations = []
    resolution = None
    errors = []
    for _ in range(count):
        started = clock()
        try:
            frame = capture.get_frame(busy_wait=busy_wait)
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
            continue
        elapsed = max(clock() - started, 0.0)
        if frame is None:
            errors.append('no frame returned')
            continue
        try:
            height, width = frame.shape[:2]
        except (AttributeError, IndexError, TypeError):
            errors.append('frame has no usable shape')
            continue
        frames += 1
        durations.append(elapsed)
        resolution = (int(width), int(height))
    total = sum(durations)
    return {
        'requested': count,
        'frames': frames,
        'resolution': resolution,
        'fps': frames / total if total > 0 else 0.0,
        'avg_ms': (total / frames * 1000) if frames else 0.0,
        'errors': errors,
    }


def format_report(device, capture_name, sample, ocr_count, tap_sent):
    """Build the human-readable validation report shown in the task log."""
    device = device or {}
    lines = [
        'ADB 模拟器技术验证',
        "设备: {nick} ({kind})".format(
            nick=device.get('nick') or device.get('address') or '未知',
            kind=device.get('device') or 'adb',
        ),
        f'截图通道: {capture_name}',
        f"分辨率: {sample.get('resolution') or '未知'}",
        "连续采样: {frames}/{requested} 帧，平均 {avg:.0f} ms/帧（约 {fps:.1f} FPS）".format(
            frames=sample.get('frames', 0),
            requested=sample.get('requested', 0),
            avg=sample.get('avg_ms', 0.0),
            fps=sample.get('fps', 0.0),
        ),
    ]
    lines.append(f'OCR: 识别到 {ocr_count} 个文本区域' if ocr_count is not None else 'OCR: 未执行')
    lines.append(f"单点点击: {'已发送' if tap_sent else '未发送/失败'}")
    if sample.get('errors'):
        lines.append('截图异常: ' + '; '.join(sample['errors'][:3]))
    lines.append('说明: 本任务只验证设备连接/截图/OCR/单点点击，不验证完整鸣潮任务流程。')
    return '\n'.join(lines)
