import re


from Mon import Logger
from src.task.BaseCombatTask import (
    BaseCombatTask, NotInCombatException, CharDeadException, ClaimRewardException,
)
from src.task.WWOneTimeTask import WWOneTimeTask

logger = Logger.get_logger(__name__)


class DomainTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.teleport_timeout = 100
        self.stamina_once = 0

    def revive_action(self):
        """副本内死亡恢复：关闭弹窗 → 退出副本 → 传最近传送点回血。"""

        # ① 关闭复活弹窗 (点按钮优先, esc 兜底, 与 BaseCombatTask 共用)
        self.close_revive_popup()

        # ② 打开退出菜单
        self.send_key('esc')
        self.sleep(1)

        # ③ 确认离开
        self.wait_click_feature('gray_confirm_exit_button',
                                relative_x=-1, raise_if_not_found=False,
                                time_out=3, click_after_delay=0.5, threshold=0.7)

        # ④ 必须确认已回到大世界队伍态后，再继续走 F2/传送流程
        if not self.wait_in_team_and_world(time_out=max(self.teleport_timeout, 120), raise_if_not_found=False):
            return False
        self.sleep(0.5)
        if self.realm_entry_at_heal_point:
            logger.info('revive_action: left realm at heal point, skip teleport')
            return True
        self.revive_at_tower_and_heal()
        return True

    def make_sure_in_world(self):
        if self.in_realm():
            self.send_key('esc', after_sleep=1)
            self.wait_click_feature('gray_confirm_exit_button', relative_x=-1, raise_if_not_found=False,
                                    time_out=3, click_after_delay=0.5, threshold=0.7, after_sleep=1)
            self.wait_in_team_and_world(time_out=self.teleport_timeout)
        else:
            self.ensure_main()

    def open_F2_book_and_get_stamina(self):
        self.openF2Book('gray_book_boss')
        return self.get_stamina()

    def farm_domain_with_recovery_loop(self, must_use, teleport_into_domain_once, max_recovery_retries=3,
                                       max_runs=0):
        """包装副本刷取循环：死亡恢复后自动从 F2 重新进入，并限制重试次数。

        max_runs > 0 时刷够次数就退出；0 表示沿用原逻辑（刷到体力耗尽）。
        """
        recovery_retries = 0
        stamina_waited = False
        stamina_retry = False
        self.realm_entry_at_heal_point = False
        self._last_recovery_reason = 'death'
        while True:
            current, _, _ = self.open_F2_book_and_get_stamina()
            if current < self.stamina_once or current < must_use:
                # Routine runs may wait once for natural stamina recovery.
                # With an unlimited configured count, cap the session to one run
                # after recovery so the routine ends with stamina instead of
                # immediately burning everything again.
                if (not stamina_waited and getattr(self, '_routine_wait_stamina', False)
                        and self.stamina_once):
                    stamina_waited = True
                    gap = getattr(self, '_routine_stamina_gap', 10)
                    if self.wait_for_stamina_enough(self.stamina_once, gap):
                        if (must_use == 0 and not max_runs
                                and getattr(self, '_routine_stamina_single_run', False)):
                            max_runs = 1
                        continue  # re-read stamina and proceed with the normal flow
                self.log_info(self.tr('not enough stamina'), notify=True)
                self.back()
                return
            teleport_into_domain_once()
            self.sleep(1)
            finished, must_use = self.farm_in_domain(must_use=must_use, max_runs=max_runs)
            if finished:
                # 次数不限时，farm_in_domain 只在体力不足(can_continue=False)时
                # 才返回 finished。若开了等待且还没等过，回顶层让等待分支判断：
                # 差一点就等恢复后补刷一轮，耗尽当天体力；缺口大则正常结束。
                # stamina_retry 只是额外保险，保证这一步最多触发一次。
                if (not max_runs and not stamina_waited and not stamina_retry
                        and getattr(self, '_routine_wait_stamina', False)
                        and getattr(self, '_routine_stamina_single_run', False)):
                    stamina_retry = True
                    continue
                return
            recovery_retries += 1
            if recovery_retries >= max_recovery_retries:
                self.log_info(self.tr('farm_domain: exceeded recovery retries ({max_recovery_retries}), stop farming').format(max_recovery_retries=max_recovery_retries),
                              notify=True)
                self.make_sure_in_world()
                return
            self.log_info(self.tr('farm_domain: recovered ({reason}), re-enter from F2 book').format(
                reason=getattr(self, '_last_recovery_reason', 'death')))
            # 死亡恢复站在信标上可从 F2 直接进本；领奖失败可能停在副本内
            # 任意位置，需先退出副本再传送，因此只在死亡时跳过传送回血。
            self.realm_entry_at_heal_point = getattr(self, '_last_recovery_reason', 'death') == 'death'
            self.sleep(1)

    def farm_in_domain(self, must_use=0, max_runs=0):
        """刷本循环；返回 (是否整段正常结束, 剩余 must_use)。

        第二项在死亡提前退出时仍会带上本局内已扣过的额度，供外层恢复循环继续传参。
        max_runs > 0 时刷够次数正常结束。
        """
        if self.stamina_once <= 0:
            raise RuntimeError(self.tr('"self.stamina_once" must be override'))
        self.info_incr('used stamina', 0)
        runs = 0
        while True:
            self.walk_until_f(time_out=4, backward_time=0, raise_if_not_found=True)
            self.pick_f()
            try:
                self.combat_once()
                self.sleep(3)
                # 领奖识别失败属于可恢复错误：转成专用异常，交给外层恢复循环
                # 走「退本 → 回大世界 → F2 重进」，而不是冒泡到框架直接停任务。
                try:
                    self.walk_to_treasure()
                    self.pick_f(handle_claim=False)
                except Exception as claim_error:
                    if isinstance(claim_error, (NotInCombatException, CharDeadException)):
                        raise
                    raise ClaimRewardException(str(claim_error)) from claim_error
            except (NotInCombatException, CharDeadException):
                self._last_recovery_reason = 'death'
                self.log_info(self.tr('farm_in_domain: death recovered, exiting domain'))
                self.make_sure_in_world()
                return False, must_use
            except ClaimRewardException as claim_error:
                self._last_recovery_reason = 'claim'
                self.log_warning(self.tr('farm_in_domain: claim reward failed ({error}), exiting domain to retry').format(error=claim_error))
                self.make_sure_in_world()
                return False, must_use
            can_continue, used = self.use_stamina(once=self.stamina_once, must_use=must_use)
            self.info_incr('used stamina', used)
            must_use -= used
            runs += 1
            self.sleep(4)
            if max_runs and runs >= max_runs:
                self.log_info(self.tr('reached configured run count ({runs})').format(runs=runs))
                break
            if not can_continue:
                self.log_info(self.tr('used all stamina'))
                break
            self.click(0.68, 0.84, after_sleep=1)  # farm again
            if confirm := self.wait_feature(
                    ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
                    raise_if_not_found=False,
                    threshold=0.6,
                    time_out=2):
                self.click(0.49, 0.55, after_sleep=0.5)  # 点击不再提醒
                self.click(confirm, after_sleep=0.5)
                self.wait_click_feature(
                    ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
                    relative_x=-1, raise_if_not_found=False,
                    threshold=0.6,
                    time_out=1)
            self.wait_in_team_and_world(time_out=self.teleport_timeout)
            self.sleep(1)
        #
        self.click(0.42, 0.84, after_sleep=2)  # back to world
        self.make_sure_in_world()
        return True, must_use
