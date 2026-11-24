"""One-shot validation of the ADB / Android-emulator control path.

This task deliberately does NOT run any game logic. It only proves that the
selected device is an ADB device, that the capture backend produces frames
(including while the emulator is in the background), that OCR can read the
frame, and that a single tap can be dispatched.
"""

from adb_probe import format_report, sample_capture
from Mon import BaseTask, FindFeature, Logger
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)


class AdbDiagnosisTask(BaseTask, FindFeature):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '🔌 ADB 模拟器技术验证'
        self.icon = task_icon('settings')
        self.description = (
            '验证安卓模拟器/ADB 设备的截图、OCR 与单点点击能力；'
            '仅在设备列表选择 MuMu/雷电等模拟器或 ADB 设备后运行。'
        )
        self.group_name = 'Diagnostics'
        self.default_config = {
            '采样帧数': 5,
            '执行点击测试': True,
        }
        self.config_type = {
            '采样帧数': {'min': 1, 'max': 30},
        }
        self.config_description = {
            '采样帧数': '连续抓取多少帧用于计算截图帧率；ADB 通道约 300ms/帧，MuMu IPC 更快',
            '执行点击测试': '开启后在屏幕中心发送一次点击，用于确认 ADB 输入通道可用',
        }

    def _log(self, message):
        self.log_info(message)

    def run(self):
        if not self.is_adb():
            self.log_error(
                '当前不是安卓模拟器/ADB 设备。请先在设备连接窗口选择模拟器或 ADB 设备。',
                notify=True,
            )
            return

        manager = self.executor.device_manager
        device = manager.get_preferred_device() or {}
        capture = manager.capture_method
        capture_name = capture.get_name() if capture is not None else '无截图通道'

        try:
            count = max(1, min(30, int(self.config.get('采样帧数', 5))))
        except (TypeError, ValueError):
            count = 5

        try:
            sample = sample_capture(capture, count=count, busy_wait=1.0)
        except Exception as exc:
            self.log_error(f'截图采样失败: {exc}', exception=exc, notify=True)
            return

        ocr_count = None
        if sample['frames']:
            try:
                frame = capture.get_frame(busy_wait=1.0)
                if frame is not None:
                    ocr_count = len(self.ocr(0, 0, 1, 1, frame=frame, log=False, screenshot=False))
            except Exception as exc:
                logger.warning(f'ADB probe OCR failed: {exc}')

        tap_sent = False
        if self.config.get('执行点击测试', True):
            try:
                self.click(self.width_of_screen(0.5), self.height_of_screen(0.5),
                           name='adb_probe_center')
                tap_sent = True
            except Exception as exc:
                self.log_error(f'ADB 点击测试失败: {exc}', exception=exc)

        self.log_info(format_report(device, capture_name, sample, ocr_count, tap_sent), notify=True)

        if not sample['frames']:
            self.log_error('截图通道没有返回任何帧，请检查 ADB 连接或切到 MuMu IPC 通道。', notify=True)
