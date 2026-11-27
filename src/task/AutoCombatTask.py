import time

from Mon import TriggerTask, Logger, TaskDisabledException
from src.Labels import Labels
from src.char.CharFactory import char_names
from src.combat.FKeyStart import F_EXTRA_LIFETIME, f_key_watcher, float_config
from src.scene.WWScene import WWScene
from src.task.BaseCombatTask import BaseCombatTask, NotInCombatException, CharDeadException
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)

# 按下 F 前后这么久之内屏幕上是「吸收」提示，这次 F 就是吸收声骸
ABSORB_PROMPT_WINDOW = 1.0
# 同一个 F 提示一直挂着时，隔这么久重读一次字
F_PROMPT_REREAD_INTERVAL = 0.5


class AutoCombatTask(BaseCombatTask, TriggerTask):
    owns_switch_healer_config = True
    _reset_combat_on_run = False
    # F 交互提示: 上一帧位置、上次读字时刻、最近一次看到「吸收」的时刻
    _f_prompt_box = None
    _f_prompt_read_at = 0.0
    _f_prompt_is_absorb = False
    _absorb_prompt_at = -1.0
    F_START_KEY = '按 F 开始战斗'
    F_DELAY_KEY = 'F 之后等待(秒)'
    F_GRACE_KEY = '没锁到目标最多撑(秒)'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.trigger_interval = 0.1
        self.name = "⚔️ Auto Combat"
        self.icon = task_icon('combat')
        self.description = "Enable auto combat in Abyss, Game World etc"
        self.last_is_click = False
        self.default_config.update({
            'Auto Target': True,
            'Use Liberation': True,
            'Check Levitator': True,
            'Switch to Healer before and after Combat': True,
            self.F_START_KEY: True,
            self.F_DELAY_KEY: 0.0,
            self.F_GRACE_KEY: 10.0,
        })
        self.config_description = {
            'Auto Target': '关闭后，只有用鼠标中键手动锁定目标才会开始自动战斗',
            'Use Liberation': '在大世界不使用大招，以节省时间',
            'Check Levitator': '切换滑翔并确认角色是否处于漂浮状态',
            'Switch to Healer before and after Combat': '战斗前后切换到治疗角色，提高角色存活率',
            self.F_START_KEY: ('在挑战/副本里按下 F 后, 不等锁定目标或血条出现就直接开始自动战斗。'
                               '大世界里按的 F 会被忽略。仅建议在矩阵/深塔/海墟等高难战斗本中使用'),
            self.F_DELAY_KEY: '按下 F 到开始战斗之间等待的秒数, 用来对齐刷怪。0 = 按下就开始',
            self.F_GRACE_KEY: ('开打后怪还没出来时, 这段时间里不判脱战、不停下找怪, 宏照常出招; '
                               '超过就回到原规则(找 3 秒怪, 找不到算脱战)。0 = 一直撑到锁到目标'),
        }
        self.op_index = 0
        self.char_features_warmed_up = False
        # 这个时刻及之前按的 F 不再触发开战
        self._f_consumed = -1.0

    # ---- 按 F 开始战斗（含「吸收声骸不算开战」判定） ----
    @property
    def f_start_enabled(self):
        return bool(self.config.get(self.F_START_KEY, True))

    @property
    def f_start_delay(self):
        return float_config(self.config, self.F_DELAY_KEY, 10.0)

    @property
    def f_grace_limit(self):
        return float_config(self.config, self.F_GRACE_KEY, 600.0)

    def f_grace_limit_seconds(self):
        return self.f_grace_limit

    def on_create(self):
        super().on_create()
        f_key_watcher.start(self.is_game_foreground)

    def is_game_foreground(self):
        hwnd = self.hwnd
        return hwnd is not None and hwnd.is_foreground()

    def last_f_press(self):
        """最近一次物理 F 的时刻。部分队伍代码自己也读 F，写在实例的 _f_pressed_at 上，取较新的。"""
        best = f_key_watcher.last_press
        try:
            best = max(best, float(self.__dict__.get('_f_pressed_at', -1.0)))
        except (TypeError, ValueError):
            pass
        return best

    def drop_pending_f_press(self):
        self._f_consumed = time.time()

    def find_f_prompt(self):
        return self.find_one(Labels.pick_up_f_hcenter_vcenter, box=self.f_search_box, threshold=0.8)

    def f_prompt_is_absorb(self, f):
        absorb = self.absorb_echo_text()
        if absorb is None:
            return False
        # 和 find_f_with_text 读同一块: F 右边的选项列表, 有一项是「吸收」就算
        text_box = f.copy(x_offset=f.width * 5.2, width_offset=f.width * 6, height_offset=4.5 * f.height,
                          y_offset=-0.8 * f.height, name='f_prompt_text')
        return bool(self.ocr(box=text_box, match=absorb))

    def note_f_prompt(self):
        """没在战斗时每帧看一眼 F 交互提示，记下最近一次看到「吸收」的时刻。"""
        f = self.find_f_prompt()
        if not f:
            self._f_prompt_box = None
            return
        now = time.time()
        last = self._f_prompt_box
        moved = last is None or abs(f.y - last.y) > f.height / 2 or abs(f.x - last.x) > f.width / 2
        if moved or now - self._f_prompt_read_at >= F_PROMPT_REREAD_INTERVAL:
            # 大世界里的 F 本来就不开战，不用读字
            self._f_prompt_is_absorb = not self.is_open_world_auto_combat() and self.f_prompt_is_absorb(f)
            self._f_prompt_read_at = now
        self._f_prompt_box = f
        if self._f_prompt_is_absorb:
            self._absorb_prompt_at = now

    def pressed_to_absorb(self, pressed):
        return abs(pressed - self._absorb_prompt_at) <= ABSORB_PROMPT_WINDOW

    def should_start_combat_by_key(self):
        # 未走 __init__ 的实例 (测试 __new__ fixture / 提前调用) 没有 F 开战状态, 直接跳过。
        # 否则 note_f_prompt -> f_search_box 会摸到未初始化的画面/配置属性而抛错。
        if '_f_consumed' not in self.__dict__:
            return False
        if self.f_start_enabled:
            try:
                self.note_f_prompt()
            except Exception as e:
                # OCR / 画面未就绪不该让整个战斗判断崩掉, 最多这次不认「吸收声骸」。
                self.log_info(f'F 提示读取失败, 跳过吸收判定: {e}')
        pressed = self.last_f_press()
        if pressed <= self._f_consumed:
            return False
        if not self.f_start_enabled:
            # 开关关着的时候按的 F, 之后打开开关也不算
            self._f_consumed = pressed
            return False
        waited = time.time() - pressed
        delay = self.f_start_delay
        if waited < delay:
            return False
        if waited > delay + F_EXTRA_LIFETIME:
            self._f_consumed = pressed
            return False
        # 读不到队伍说明还在读条, 先不消耗这次按键, 下一帧再试
        if not self.load_chars():
            return False
        self._f_consumed = pressed
        if self.is_open_world_auto_combat():
            self.log_info('F 开始战斗: 大世界里按的 F, 忽略')
            return False
        if self.pressed_to_absorb(pressed):
            # Boss 副本也算副本, 打完按 F 捡声骸以前会被当成开战
            self.log_info('F 开始战斗: 按的是吸收声骸, 忽略')
            return False
        return True

    def start_combat_by_key(self):
        self.has_lavitator = self.find_one('edge_levitator', threshold=0.65)
        self._in_combat = True
        now = time.time()
        self._f_grace_since = now
        self._f_grace_log = now
        # find_one 要排在中键前面: click() 结尾会 reset_scene()
        if self.config.get('Auto Target', True):
            self.middle_click()
        self.scene.set_in_combat()
        self.log_info(f'enter combat by physical F {self.has_lavitator}')
        return True

    def enable(self):
        # 打开自动战斗之前按的 F 不算
        self.drop_pending_f_press()
        super().enable()

    def combat_end(self):
        # 这场战斗里和之前按的 F(处决、捡东西) 不能再开一场
        self.drop_pending_f_press()
        self._f_grace_since = -1.0
        super().combat_end()

    def disable(self):
        # The UI thread only invalidates state; the executor performs the reset.
        self._reset_combat_on_run = True
        # 自动战斗停了就不再需要 50ms 轮询 GetAsyncKeyState 的常驻线程;
        # 按"任务结束不留后台脚本"的口径在 disable 里停掉, 重新启用时 run() 会重启。
        try:
            f_key_watcher.stop()
        except Exception:
            pass
        super().disable()

    def reset_auto_combat(self, reason):
        self._reset_combat_on_run = False
        self.reset_to_false(reason)
        self.chars = [None, None, None]
        self.freeze_durations = []

    def warm_up_char_features(self):
        if self.char_features_warmed_up:
            return
        try:
            for char_name in char_names:
                self.get_feature_by_name(char_name)
        except Exception as e:
            logger.warning(f'warm_up_char_features failed: {e}')
            return
        self.char_features_warmed_up = True
        logger.info(f'warm_up_char_features loaded {len(char_names)} character templates')

    def run(self):
        # 重新启用时 on_create 不会再次触发, 在这里确保 F 监听线程活着。
        try:
            f_key_watcher.start(self.is_game_foreground)
        except Exception:
            pass
        if self._reset_combat_on_run:
            self.reset_auto_combat('auto combat restarted')
        try:
            return self.run_auto_combat()
        except TaskDisabledException:
            self.reset_auto_combat('auto combat disabled')
            raise

    def run_auto_combat(self):
        self.warm_up_char_features()
        ret = False
        if not self.scene.in_team(self.in_team_and_world):
            self.reset_auto_combat('not in team and world')
            return ret
        self.use_liberation = self.config.get('Use Liberation')
        if not self.use_liberation and not self.in_world():  # 仅大世界生效
            self.use_liberation = True
        combat_start = time.time()
        switched_to_healer = False
        while self.in_combat():
            ret = True
            try:
                if not switched_to_healer:
                    self.switch_healer()
                    switched_to_healer = True
                self.get_current_char().perform()
            except CharDeadException:
                self.log_error(self.tr('Characters dead'), notify=True)
                break
            except NotInCombatException as e:
                logger.info(f'auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}')
                break
        if ret:
            self.combat_end()
            self.switch_healer()
        return ret

    def realm_perform(self):
        if not self.last_is_click:
            if self.op_index % 10 == 0:
                self.send_key_and_wait_animation('4', self.in_illusive_realm, enter_animation_wait=0.2)
            else:
                self.click()
        else:
            if self.available('liberation'):
                self.send_key_and_wait_animation(self.get_liberation_key(), self.in_illusive_realm)
            elif self.available('echo'):
                self.send_key(self.get_echo_key())
            elif self.available('resonance'):
                self.send_key(self.get_resonance_key())
            elif self.is_con_full() and self.in_team()[0]:
                self.send_key_and_wait_animation('2', self.in_illusive_realm)
        self.last_is_click = not self.last_is_click
        self.op_index += 1
        self.sleep(0.02)


from Mon import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=AutoCombatTask, debug=True)
