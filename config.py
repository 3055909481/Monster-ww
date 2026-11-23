import os
import re
from pathlib import Path

import pyappify

from Mon import Box, ConfigOption, Icon
from Mon.util.GlobalConfig import create_basic_options
from src.game_launcher import get_game_launch_arguments
from src.task.process_feature import process_feature
from src.task.task_icons import task_icon

# The framework enables its update card when this launcher API is callable.
# Install our GitHub-release-backed updater so the existing update UI works:
# check button, version picker, automatic startup check and update-then-restart.
# Pure runtime use (workshop, tasks) stays offline; only an explicit update
# check or the framework's startup check touches the network.
try:
    from src import updater as _updater
    _updater.install()
except Exception:
    # Never let an updater problem prevent the application from starting.
    pyappify.get_version_list = None

version = "dev"


def _appdata_config_folder():
    """User data lives under %APPDATA%/Monster-ww so full package replacement
    never touches accounts, schedules or run history. Falls back to the legacy
    in-app "configs" folder on non-Windows or when APPDATA is unavailable."""
    try:
        base = os.environ.get("APPDATA")
        if not base:
            import tempfile
            base = os.path.join(tempfile.gettempdir(), "Monster-ww-data")
        path = os.path.join(base, "Monster-ww", "configs")
        os.makedirs(path, exist_ok=True)
        return path
    except (OSError, TypeError):
        return "configs"


CONFIG_FOLDER = _appdata_config_folder()


def _find_most_recently_run_pc_exe():
    try:
        import codecs
        import struct
        import winreg
    except ImportError:
        return None

    user_assist_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    candidates = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_assist_key) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                guid = winreg.EnumKey(root, index)
                try:
                    with winreg.OpenKey(root, fr"{guid}\Count") as count_key:
                        for value_index in range(winreg.QueryInfoKey(count_key)[1]):
                            encoded_path, data, _ = winreg.EnumValue(count_key, value_index)
                            path = os.path.expandvars(codecs.decode(encoded_path, "rot_13"))
                            if not path.casefold().endswith(r"\wuthering waves.exe"):
                                continue
                            if not Path(path).is_file():
                                continue
                            last_run = struct.unpack_from("<Q", data, 60)[0] if len(data) >= 68 else 0
                            candidates.append((last_run, path))
                except OSError:
                    continue
    except OSError:
        return None

    return max(candidates, default=(0, None))[1]


def _find_pc_exe_from_registry():
    try:
        import winreg
    except ImportError:
        return None

    uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    registry_views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in registry_views:
            try:
                with winreg.OpenKey(root, uninstall_key, 0, winreg.KEY_READ | view) as key:
                    subkey_count = winreg.QueryInfoKey(key)[0]
                    for index in range(subkey_count):
                        try:
                            subkey_name = winreg.EnumKey(key, index)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                display_name = _read_registry_value(subkey, "DisplayName", winreg)
                                if not _is_wuthering_waves_registry_entry(subkey_name, display_name):
                                    continue
                                for value_name in (
                                        "InstallPath", "InstallLocation", "LauncherPath",
                                        "DisplayIcon", "UninstallString"):
                                    registered_path = _read_registry_value(subkey, value_name, winreg)
                                    if game_exe := _find_pc_exe_near_registered_path(registered_path):
                                        return game_exe
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def _read_registry_value(key, name, winreg):
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _is_wuthering_waves_registry_entry(subkey_name, display_name):
    names = f"{subkey_name} {display_name}".casefold()
    return "wuthering waves" in names or "鸣潮" in names or "鳴潮" in names


def _find_pc_exe_near_registered_path(registered_path):
    if not registered_path:
        return None

    path_text = os.path.expandvars(registered_path.strip().strip('"'))
    exe_end = path_text.casefold().find(".exe")
    if exe_end >= 0:
        path_text = path_text[:exe_end + 4]

    registered = Path(path_text)
    install_folder = registered.parent if registered.suffix.casefold() == ".exe" else registered
    candidates = (
        install_folder / "Wuthering Waves.exe",
        install_folder / "Wuthering Waves Game" / "Wuthering Waves.exe",
        install_folder.parent / "Wuthering Waves Game" / "Wuthering Waves.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


# 客户端服选择：国服 / 国际服。两台客户端 exe 同名（Wuthering Waves.exe），
# 只是安装目录不同，因此用「服务器 → 完整路径」配置区分，空值回退自动探测。
SERVER_CN = '国服'
SERVER_GLOBAL = '国际服'
SERVER_OPTIONS = (SERVER_CN, SERVER_GLOBAL)
SERVER_KEY = 'Game Server'
SERVER_PATH_KEYS = {SERVER_CN: 'CN Client Path', SERVER_GLOBAL: 'Global Client Path'}
# 多账号执行时按账号临时切换服；None 表示跟随全局配置。
_server_override = None


def set_server_override(server):
    """Temporarily select a server (used per-account); None restores global."""
    global _server_override
    _server_override = server if server in SERVER_OPTIONS else None


def get_current_server():
    """Resolve the active server: per-account override first, then global config."""
    if _server_override in SERVER_OPTIONS:
        return _server_override
    try:
        from Mon import og
        value = og.global_config.get_config('Basic Options').get(SERVER_KEY)
        if value in SERVER_OPTIONS:
            return value
    except Exception:
        pass
    return SERVER_CN


def configured_client_path(server=None):
    """Return the user-configured launcher path for a server, if it still exists."""
    server = server or get_current_server()
    key = SERVER_PATH_KEYS.get(server)
    if not key:
        return None
    try:
        from Mon import og
        value = og.global_config.get_config('Basic Options').get(key)
    except Exception:
        return None
    if isinstance(value, str) and value.strip():
        path = Path(os.path.expandvars(value.strip().strip('"')))
        if path.is_file():
            return str(path)
    return None


def calculate_pc_exe_path(running_path=None):
    # An explicit per-server path always wins: it is the whole point of the
    # dual-client config. Fall back to auto-detection only when unset/missing.
    explicit = configured_client_path()
    if explicit:
        return explicit
    if running_path is None:
        return _find_most_recently_run_pc_exe() or _find_pc_exe_from_registry()
    game_exe_folder = Path(running_path).parents[3]
    return str(game_exe_folder / "Wuthering Waves.exe")


def blur_area(width, height):
    blur_width = int(0.12 * width)
    blur_height = int(0.024 * height)
    return Box(width * 0.879, height * 0.976, blur_width * 0.973, blur_height * 0.994)


basic_config_option = create_basic_options(enable_blur=True)

# 自适应触发间隔的五个键并入 Basic Options，和 Trigger Interval 同处 Tasks 分组。
# key 保持英文以兼容已有配置存储；界面显示由 i18n/zh_CN 提供中文翻译。
_ADAPTIVE_KEYS = {
    'Enable Adaptive Interval': True,
    'Combat Interval Min (ms)': 1,
    'Combat Interval Max (ms)': 1,
    'Idle Interval Min (ms)': 25,
    'Idle Interval Max (ms)': 35,
}
basic_config_option.default_config.update(_ADAPTIVE_KEYS)
_basic_type = basic_config_option.config_type or {}
for _k in _ADAPTIVE_KEYS:
    _basic_type[_k] = {'group': 'Tasks', **({'min': 0} if '(ms)' in _k else {})}
basic_config_option.config_type = _basic_type
_basic_desc = basic_config_option.config_description
_basic_desc.update({
    'Enable Adaptive Interval': '开启后按下面的范围轮询；关闭则完全恢复框架默认行为',
    'Combat Interval Min (ms)': '战斗轮询间隔下限（毫秒），默认 1ms 与框架原值一致，上下限相同即为固定值',
    'Combat Interval Max (ms)': '战斗轮询间隔上限（毫秒），默认与下限相同保持固定节奏，不建议改小',
    'Idle Interval Min (ms)': '空闲轮询间隔下限（毫秒），越大越省资源，但启动任务的响应稍慢',
    'Idle Interval Max (ms)': '空闲轮询间隔上限（毫秒），与下限拉开可打散CPU/GPU占用尖峰；填反会自动纠正',
})

# 点击坐标微抖动：提升按钮命中率（功能稳定性，非反检测）。并入 Basic Options。
_CLICK_JITTER_KEYS = {
    'Enable Click Jitter': True,
    'Click Jitter Radius (px)': 2,
}
basic_config_option.default_config.update(_CLICK_JITTER_KEYS)
for _k in _CLICK_JITTER_KEYS:
    _basic_type[_k] = {'group': 'Tasks', 'min': 0}
_basic_desc.update({
    'Enable Click Jitter': '开启后点击坐标在极小范围内抖动，提升按钮命中率；关闭则恢复固定坐标',
    'Click Jitter Radius (px)': '抖动半径（像素），默认 2，最大 8；中心仍是原坐标，不会系统性偏移',
})

# 客户端服选择与路径：放在 Game 分组，紧邻「Auto Start Game When App Starts」。
# 留空则沿用原有的「最近运行 → 注册表」自动探测，老用户无需改动即可保持原行为。
_SERVER_OPTION_KEYS = (SERVER_KEY, *SERVER_PATH_KEYS.values())
basic_config_option.default_config.update({SERVER_KEY: SERVER_CN,
                                           SERVER_PATH_KEYS[SERVER_CN]: '',
                                           SERVER_PATH_KEYS[SERVER_GLOBAL]: ''})
_basic_type[SERVER_KEY] = {'group': 'Game', 'type': 'drop_down', 'options': list(SERVER_OPTIONS)}
for _path_key in SERVER_PATH_KEYS.values():
    _basic_type[_path_key] = {'group': 'Game', 'type': 'file_selector', 'selector_type': 'file'}
_basic_desc.update({
    SERVER_KEY: '选择要启动的鸣潮客户端服务器（国服/国际服）；多账号任务可按账号单独覆盖',
    SERVER_PATH_KEYS[SERVER_CN]: '国服客户端 Wuthering Waves.exe 完整路径；留空则自动探测',
    SERVER_PATH_KEYS[SERVER_GLOBAL]: '国际服客户端 Wuthering Waves.exe 完整路径；留空则自动探测',
})

key_config_option = ConfigOption('Game Hotkey', {
    'Echo Key': 'q',
    'Liberation Key': 'r',
    'Resonance Key': 'e',
    'Tool Key': 't',
    'Jump Key': 'space',
    'Dodge Key': 'lshift',
    'Wheel Key': 'tab',
    'Guidebook Key': 'f2',
    'Bag Key': 'b',
}, description='In Game Hotkey for Skills', config_description={
    'Bag Key': 'In-game hotkey used to open the Bag.',
}, show_at_tab=True, icon=Icon.GAME)

char_config_option = ConfigOption('Character Config', {
    'Iuno C6': False,
    'Roccia C6': False,
    'Chisa DPS': False,
    'Suoming DPS': False,
}, description='Character Config', show_at_tab=True, icon=Icon.PEOPLE)

monthly_card_config_option = ConfigOption('Monthly Card Config', {
    'Check Monthly Card': True,
    'Monthly Card Time': 4
}, description='Turn on to avoid interruption by monthly card when executing tasks', config_description={
    'Check Monthly Card': 'Check for monthly card to avoid interruption of tasks',
    'Monthly Card Time': 'Your computer\'s local time when the monthly card will popup, hour in (1-24)'
})

config = {
    'debug': False,  # Optional, default: False
    'custom_tasks': True,
    "gui": {
        "type": "qt",
    },
    'task_category_icons': {
        'all': task_icon('category-all'),
        'onetime': task_icon('category-onetime'),
        'trigger': task_icon('category-trigger'),
    },
    'task_category_colors': {
        'all': {'light': '#526174', 'dark': '#B5BECF'},
        'onetime': {'light': '#087C70', 'dark': '#72D9CC'},
        'trigger': {'light': '#AC3652', 'dark': '#F09AA8'},
        'group:Daily Activities': {'light': '#2668AA', 'dark': '#8ABCF0'},
        'group:Echo Management': {'light': '#8C651A', 'dark': '#E8C27C'},
        'group:Multi-account': {'light': '#7650A5', 'dark': '#C2A3EA'},
        'group:版本活动': {'light': '#B4682A', 'dark': '#F0AE72'},
    },
    'config_folder': CONFIG_FOLDER,
    'blur_area': blur_area,
    'gui_icon': 'icons/icon.png',
    'global_configs': [basic_config_option, key_config_option, char_config_option, monthly_card_config_option],
    # Team / character-code editor. Upstream unregistered this page (commits
    # 8979325e / aa543c43) but still ships its implementation, tests and i18n,
    # so re-enable it here for the Monster-ww build.
    'custom_tabs': [
        ["src.gui.CharacterCodeTab", "CharacterCodeTab"],
        ["src.gui.RoutineTab", "RoutineTab"],
    ],
    'ocr': {
        'lib': 'onnxocr',
        'auto_simplify': True,
        'params': {
            'use_openvino': True,
            'use_npu': True,
        }
    },
    'plugin':True,
    'my_app': ['src.globals', 'Globals'],
    'start_timeout': 120,  # default 60
    'update_only_game_exit': True,  # if startup skipped the update check, check 10s after the game exits
    'wait_until_settle_time': 0,
    # required if using feature detection
    'template_matching': {
        'template_folder': os.path.join('assets', 'templates'),
        'default_horizontal_variance': 0.002,
        'default_vertical_variance': 0.002,
        'default_threshold': 0.8,
        'feature_processor': process_feature,
        'vcenter_features': ['monthly_card', 'skip_dialog_check'],
        'hcenter_features': ['monthly_card', 'suisui_forte3', 'message_dialog', 'claim_stamina_sign',
                             'skip_dialog_check', 'skip_quest_confirm', 'skip_quest_confirm_new',
                             'login_close', 'garden_confirm', 'garden_continue_game',
                             'garden_unpause', 'garden_get_gold', 'garden_get_purple', 'garden_get_skip',
                             'garden_not_interested_confirm', 'garden_not_interested', 'a_garden_back',
                             'garden_get_confirm_gray', 'the_garden_max', 'garden_shop_close', 'garden_new_stage',
                             'a_garden_restart', 'suisui_forte2', 'e_forte', 'f_break_full']
    },
    'windows': {  # required  when supporting windows game
        'top_hwnd_class': [re.compile('CAgreementDlg'), re.compile('CLoginDlg_P_'),
                           'CefBrowserWindow', 'Chrome_RenderWidgetHostHWND', '#32770',
                           re.compile('CNativeLoginDlg'), 'Static', 'ComboBox', 'ComboLBox', 'Button'
                           ],
        'calculate_pc_exe_path': calculate_pc_exe_path,
        'launch_arguments': get_game_launch_arguments,
        'exe': 'Client-Win64-Shipping.exe',
        'hwnd_class': 'UnrealWindow',
        # The framework renders this list as the "Choose Interaction" selector and
        # persists the user's choice across restarts. Keep PostMessage first so the
        # default stays the background mode. Pynput / PyDirect are foreground modes
        # that send real OS-level input (mouse/keyboard events) instead of posting
        # window messages, which Unreal Engine games usually honour more reliably.
        # Foreground modes require the game window to stay in front and admin rights.
        'interaction': ['PostMessage', 'Pynput', 'PyDirect'],
        'capture_method': ['WGC', 'WGC_HDR','BitBlt_RenderFull'],  # Windows版本支持的话, 优先使用WGC, 否则使用BitBlt_Full
        'check_hdr': False,
        'force_no_hdr': False,
        'check_auto_hdr': True,
        'check_night_light': True,
        'force_no_night_light': False,
    },
    # 云游戏（需要 Win11 并安装 Chrome 或 Edge）。框架的 browser 设备据此启用。
    'browser': {
        'url': 'https://mc.kurogames.com/cloud/#/',
        'resolution': (1920, 1080),
        'channels': ['msedge', 'chrome', 'chromium'],
        'args': ['--webrtc-ip-handling-policy=default', '--force-color-profile=srgb'],
        'nick': '云游戏(需要Win11并安装Chrome或Edge)',
    },
    'window_size': {
        'width': 1200,
        'height': 800,
        'min_width': 1200,
        'min_height': 800,
    },
    'supported_resolution': {
        'ratio': '16:9',
        'resize_to': [(2560, 1440), (1920, 1080), (1600, 900), (1280, 720)],
        'min_size': (1280, 720)
    },
    'links': {
        'default': {
            'github': 'https://github.com/3055909481/Monster-ww',
            'faq': 'https://github.com/3055909481/Monster-ww'
        },
        'zh_CN': {
            'github': 'https://github.com/3055909481/Monster-ww',
            'faq': 'https://github.com/3055909481/Monster-ww',
        },
    },
    'about': """
    <p style="color:red;">
    <strong>本软件是免费开源的。</strong> 如果你被收费，请立即退款。
    </p>
    <p style="color:red;">
        <strong>本软件仅供个人使用，用于学习Python编程、计算机视觉、UI自动化等。</strong> 请勿将其用于任何营利性或商业用途。
    </p>
    <p style="color:red;">
        <strong>使用本软件可能会导致账号被封。</strong> 请在了解风险后再使用。
    </p>
""",
    'screenshots_folder': "screenshots",
    'gui_title': 'Monster-ww',  # Optional
    'game_name': 'Wuthering Waves',
    # 'coco_feature_folder': get_path(__file__, 'assets/coco_feature'),  # required if using feature detection
    'log_file': 'logs/Monster-ww.log',  # Optional, auto rotating every day
    'error_log_file': 'logs/Monster-ww_error.log',
    'launcher_log_file': 'logs/launcher.log',
    'launcher_error_log_file': 'logs/launcher_error.log',
    'version': version,
    'onetime_tasks': [  # tasks to execute
        ["src.task.DailyTask", "DailyTask"],
        ["src.task.FarmEchoTask", "FarmEchoTask"],
        ["src.task.NightmareNestTask", "NightmareNestTask"],
        ["src.task.TacetTask", "TacetTask"],
        ["src.task.ForgeryTask", "ForgeryTask"],
        ["src.task.SimulationTask", "SimulationTask"],
        ["src.task.CultivationTask", "CultivationTask"],
        ["src.task.MultiAccountDailyTask", "MultiAccountDailyTask"],
        ["src.task.MultiAccountConfigTask", "MultiAccountConfigTask"],
        ["src.task.MergeEchoTask", "MergeEchoTask"],
        ["src.task.EnhanceEchoTask", "EnhanceEchoTask"],
        ["src.task.ChangeEchoTask", "ChangeEchoTask"],
        ["src.task.GardenTask", "GardenTask"],
        ["src.task.TacetEliminationTask", "TacetEliminationTask"],
        ["src.task.RoutineTask", "RoutineTask"],
        ["src.task.TrainingGuideTask", "TrainingGuideTask"],
        # 版本活动（3.8）：单列分组，不并入日常活动
        ["src.task.GalGameTask", "GalGameTask"],
        ["src.task.RahaiBlocksTask", "RahaiBlocksTask"],
        # ["src.task.DiagnosisTask", "DiagnosisTask"],
    ], 'trigger_tasks': [
        ["src.task.AutoCombatTask", "AutoCombatTask"],
        ["src.task.AutoPickTask", "AutoPickTask"],
        ["src.task.AutoLoginTask", "AutoLoginTask"],
        ["src.task.SkipDialogTask", "AutoDialogTask"],
        ["src.task.FastTravelTask", "FastTravelTask"],
        ["src.task.MouseResetTask", "MouseResetTask"],
    ], 'scene': ["src.scene.WWScene", "WWScene"],
    'update_pyappify': {
        'to_version': '1.2.3',
        'zip_url': 'https://github.com/3055909481/Monster-ww/releases/latest',
        'sha256': '447207edbfb6944beb994347048e155df99775eb18c87dcae54dda125071c94a',
    }

}
