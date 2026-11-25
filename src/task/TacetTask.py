import re

from Mon import Logger
from src.task.BaseCombatTask import (
    BaseCombatTask, CharDeadException, CharRevivedException, ClaimRewardException,
)
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)
RECHALLENGE_TEXT = re.compile(r'^\s*(?:重新挑战|重新挑戰|再次挑战|再次挑戰|Rechallenge)\s*$', re.IGNORECASE)


class TacetTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.description = "Farms the selected Tacet Suppression, until no stamina. Must be able to teleport (F2)."
        self.name = "🌊 Tacet Suppression"
        self.icon = task_icon('tacet')
        self.group_name = 'Daily Activities'
        self.group_icon = task_icon('category-daily')
        self.support_schedule_task = True
        default_config = {
            'Which Tacet Suppression to Farm': 1,  # starts with 1
        }
        self.structure = [4, 5, 5, 7]
        self.total_number = sum(self.structure)
        self.target_enemy_time_out = 10
        default_config.update(self.default_config)
        self.config_description = {
            'Which Tacet Suppression to Farm': 'The Tacet Suppression number in the F2 list.',
        }
        self.default_config = default_config
        self.door_walk_method = {  # starts with 0
            0: [],
            1: [],
            2: [],
            3: [],
            4: [],
            5: [],
            6: [],
            7: [],
            8: [],
            9: [["a", 0.3]],
            10: [["d", 0.6]],
            11: [["a", 1.5], ["w", 3], ["a", 2.5]],
        }
        self.stamina_once = 60

    def run(self):
        super().run()
        self.ensure_main(time_out=180)
        self.wait_in_team_and_world(esc=True)
        self.farm_tacet()

    def farm_tacet(self, daily=False, used_stamina=0, config=None, max_recovery_retries=3, max_runs=0,
                   open_entry=None, enter_tacet=None):
        """Farm Tacet Suppression until stamina runs out.

        open_entry/enter_tacet let other tasks reuse this loop from another page:
        open_entry() must show the stamina bar, enter_tacet() must open the team screen.
        Both default to the F2 Tacet list selected by config.
        """
        if config is None:
            config = self.config
        if daily:
            must_use = 180 - used_stamina
        else:
            must_use = 0
        self.info_incr('used stamina', 0)
        recovery_retries = 0
        runs = 0
        stamina_waited = False
        self.realm_entry_at_heal_point = False
        while True:
            self.sleep(1)
            if open_entry is None:
                self.openF2Book("gray_book_boss")
            else:
                open_entry()
            current, _, _ = self.get_stamina()
            if open_entry is None and current == -1:
                self.click_relative(0.04, 0.4, after_sleep=1)
                current, _, _ = self.get_stamina()
            if current < self.stamina_once:
                # Routine runs may wait once for natural recovery. An unlimited
                # count is capped to one run after the wait so the task ends with
                # stamina rather than burning it all again.
                if (not stamina_waited and getattr(self, '_routine_wait_stamina', False)
                        and self.stamina_once):
                    stamina_waited = True
                    gap = getattr(self, '_routine_stamina_gap', 10)
                    if self.wait_for_stamina_enough(self.stamina_once, gap):
                        current, _, _ = self.get_stamina()
                        if current >= self.stamina_once:
                            if (must_use == 0 and not max_runs
                                    and getattr(self, '_routine_stamina_single_run', False)):
                                max_runs = 1
                            continue
                result = self.not_enough_stamina()
                # Hook callers (CultivationTask) expect a spent-stamina count;
                # default callers keep the historical None return.
                return 0 if open_entry is not None else result

            if enter_tacet is None:
                self.open_boss_book('wuyin')
                index = config.get('Which Tacet Suppression to Farm', 1) - 1
                self.teleport_to_tacet(index)
            else:
                enter_tacet()
            self.click_team_challenge()
            while True:
                self.wait_in_team_and_world(time_out=120)
                try:
                    self.clear_tacet_and_reach_reward()
                except CharDeadException as death_error:
                    # 两种死亡都要接住并退本重进, 否则体力刷不完:
                    # - CharRevivedException: 原地复活成功
                    # - CharDeadException: 复活失败(传送回血超时等), 曾直接冒泡
                    #   到 RoutineTask 中断整步, 导致体力没用完。
                    recovery_retries += 1
                    if recovery_retries >= max_recovery_retries:
                        self.log_info(self.tr('farm_tacet: exceeded recovery retries ({max_recovery_retries}), stop farming').format(max_recovery_retries=max_recovery_retries),
                                      notify=True)
                        try:
                            self.ensure_main(time_out=120)
                        except Exception:
                            pass  # 清理失败不应掩盖已到上限的退出
                        return None
                    revived = isinstance(death_error, CharRevivedException)
                    self.log_info(self.tr('farm_tacet: {kind}, re-enter from F2 book').format(
                        kind='death recovered' if revived else 'revive failed'))
                    # 复活成功站在信标上可直接 F2 进本；复活失败状态未知，
                    # 先 ensure_main 退本回大世界, 再完整走传送流程重进。
                    if not revived:
                        try:
                            self.ensure_main(time_out=120)
                        except Exception as cleanup_error:
                            self.log_warning(self.tr('Failed to leave realm after revive failure: {error}').format(error=cleanup_error))
                    self.realm_entry_at_heal_point = revived
                    break
                except ClaimRewardException as claim_error:
                    # 领奖识别失败：计入重试上限，退本回主页后从 F2 重新进本，
                    # 而不是冒泡到框架直接停任务。
                    recovery_retries += 1
                    if recovery_retries >= max_recovery_retries:
                        self.log_info(self.tr('farm_tacet: exceeded recovery retries ({max_recovery_retries}), stop farming').format(max_recovery_retries=max_recovery_retries),
                                      notify=True)
                        self.ensure_main(time_out=120)
                        return None
                    self.log_warning(self.tr('farm_tacet: claim reward failed ({error}), exiting domain to retry').format(error=claim_error))
                    self.ensure_main(time_out=120)
                    self.realm_entry_at_heal_point = False
                    break
                self.pick_f(handle_claim=False)
                self.sleep(2)
                if not self.has_claim_stamina():
                    # 领奖标记未出现也计一次恢复。连续失败时退本重进，避免
                    # 原逻辑 esc_cancel()+continue 在同一状态无限循环。
                    recovery_retries += 1
                    if recovery_retries >= max_recovery_retries:
                        self.log_info(self.tr('farm_tacet: exceeded recovery retries ({max_recovery_retries}), stop farming').format(max_recovery_retries=max_recovery_retries),
                                      notify=True)
                        self.ensure_main(time_out=120)
                        return None
                    self.esc_cancel()
                    self.log_info(self.tr('is not claim treasure, re-enter from F2 book ({attempt}/{max})').format(
                        attempt=recovery_retries, max=max_recovery_retries))
                    self.ensure_main(time_out=120)
                    self.realm_entry_at_heal_point = False
                    break
                can_continue, used = self.use_stamina(once=self.stamina_once, must_use=must_use)
                self.info_incr('used stamina', used)
                self.sleep(4)
                runs += 1
                if max_runs and runs >= max_runs:
                    self.click_relative(0.365, 0.853, hcenter=True)
                    self.wait_in_team_and_world(time_out=120)
                    self.log_info(self.tr('reached configured run count ({runs})').format(runs=runs))
                    return None
                if not can_continue:
                    self.click_relative(0.365, 0.853, hcenter=True)
                    self.wait_in_team_and_world(time_out=120)
                    # 次数不限刷到体力不足时，若开了等待且还没等过，等恢复后
                    # 补刷一轮再结束，尽量把当天恢复的体力耗尽。
                    if (not max_runs and not stamina_waited
                            and getattr(self, '_routine_wait_stamina', False)
                            and getattr(self, '_routine_stamina_single_run', False)
                            and self.stamina_once):
                        stamina_waited = True
                        gap = getattr(self, '_routine_stamina_gap', 10)
                        if self.wait_for_stamina_enough(self.stamina_once, gap):
                            max_runs = runs + 1  # 补刷一轮后由次数判断正常结束
                            break  # 回外层重新进本
                    return None
                else:
                    self.rechallenge_tacet()
                must_use -= used

    def clear_tacet_and_reach_reward(self):
        """打完无音区并走到领奖点；领奖点超时但仍在副本且战斗未结束时续战再走。

        续战一次后仍识别不到领奖点时抛 ClaimRewardException，交由 farm_tacet
        的恢复循环退本重进（不再把普通识别失败冒泡到框架停任务）。
        """
        try:
            self.combat_once(target=True)
            self.walk_to_treasure()
        except CharDeadException:
            raise  # 死亡/复活失败原样上抛, 交 farm_tacet 退本重进
        except Exception as first_error:
            if not self.in_realm() or not self.wait_combat(target=True, time_out=5, raise_if_not_found=False):
                raise ClaimRewardException(str(first_error)) from first_error
            logger.info('Tacet reward is unavailable and combat is still active. Resuming in the same arena.')
            self.combat_once(target=True, wait_combat_time=5)
            try:
                self.walk_to_treasure()
            except CharDeadException:
                raise
            except Exception as second_error:
                raise ClaimRewardException(str(second_error)) from second_error

    def find_rechallenge_button(self):
        buttons = self.ocr(0.54, 0.81, 0.72, 0.90, match=RECHALLENGE_TEXT)
        return buttons[0] if buttons else None

    def rechallenge_tacet(self):
        """优先 OCR 识别「重新挑战」按钮，找不到时沿用旧固定坐标兜底。"""
        button = self.wait_until(self.find_rechallenge_button, time_out=3, raise_if_not_found=False)
        if button:
            self.click(button, after_sleep=0.2)
        else:
            self.click_relative(0.640, 0.851, hcenter=True, after_sleep=0.2)
        self.wait_click_skip_dialog_confirm()

    def not_enough_stamina(self, back=True):
        self.log_info(self.tr('used all stamina'))
        if back:
            self.back(after_sleep=1)

    def teleport_to_tacet(self, index):
        self.info_set('Teleport to Tacet Suppression', index)
        if index >= self.total_number:
            raise IndexError(self.tr('Index out of range, max is {total_number}').format(total_number=self.total_number))
        return self.click_on_book_target(index + 1, self.total_number, self.structure)
