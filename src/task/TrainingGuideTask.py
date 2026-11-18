"""Fully automatic training-guide material farmer.

Flow::

    ensure_main -> open 素材获取 -> 培养目标 -> 所需材料 page
    -> OCR rows showing "直接挑战" + a "have/need" number
    -> for each row with have < need: click 直接挑战 -> fight -> exit
    -> reopen the panel -> repeat until no challengeable shortage remains

The game itself decides which domain a row leads to, so this task never needs
material names, group->domain mapping, or domain locators. It only trusts two
things on screen: the ``直接挑战`` button and the ``have/need`` number.

All screen-dependent constants live in ``Config`` below so they can be tuned
after a real-machine run. OCR/模板 based detection is used wherever possible;
the fallbacks are deliberately explicit and log screenshots on failure.
"""

import re

from Mon import Logger

from src.task.DomainTask import DomainTask
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)

NUMBER_RE = re.compile(r'(\d+)\s*/\s*(\d+)')


class Config:
    """Tunable screen/flow parameters (calibrate on a real machine if needed)."""

    # How to open the 素材获取 panel. Each candidate is tried in order:
    #   ("ocr", text)      -> find the text on screen via OCR and click it
    #   ("key", key_name)  -> read a key from key_config and send it
    #   ("click", x, y)    -> click a relative coordinate
    # Set OPEN_PANEL_STEPS to the strategy that works on your machine.
    OPEN_PANEL_STEPS = [
        ('ocr', '素材获取'),
        ('ocr', '培养目标'),
        ('key', 'Guidebook Key'),
    ]

    # Wait timeouts (seconds)
    WAIT_PANEL_TIMEOUT = 6
    WAIT_COMBAT_TIMEOUT = 15
    COMBAT_TIMEOUT = 180
    EXIT_TIMEOUT = 15

    # Max rows to process in one run (safety cap; 0 = unlimited)
    MAX_ROWS = 20
    # Max retries when a row fails to produce a combat
    ROW_RETRIES = 1

    # OCR region for the material rows (relative screen coords).
    # Default = right half where the row cards are. Narrow it if needed.
    ROWS_REGION = (0.36, 0.12, 0.99, 0.92)
    CHALLENGE_REGION = (0.36, 0.12, 0.99, 0.92)

    # Vertical tolerance (relative) to pair a number with a 直接挑战 button on the same row.
    SAME_ROW_TOLERANCE = 0.05

    # Text variants for the direct-challenge button (game may localize).
    CHALLENGE_TEXTS = ('直接挑战', '挑战')

    # Extra sleep after clicking a challenge button / after exiting a domain.
    AFTER_CLICK_SLEEP = 2
    AFTER_EXIT_SLEEP = 2


class TrainingGuideTask(DomainTask):
    """Automatically farm every shortage that offers a direct challenge.

    Inherits DomainTask so ``combat_once``, ``farm_in_domain`` and
    ``make_sure_in_world`` are all available after the game drops us into a
    domain via the 直接挑战 button.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '培养目标自动补齐'
        self.icon = task_icon('daily')
        self.group_name = 'Daily Activities'
        self.group_icon = task_icon('category-daily')
        self.description = 'Auto-farm material shortages that offer a direct challenge.'
        self.support_schedule_task = True
        self.default_config['每行刷取次数上限'] = 10
        self.config_description['每行刷取次数上限'] = '单个缺口行最多重复挑战次数；0 表示刷到无缺口。'
        self.config_type['每行刷取次数上限'] = {'min': 0, 'max': 99}
        self.add_exit_after_config()
        self.processed = 0

    # ------------------------------------------------------------------ run
    def run(self):
        DomainTask.run(self)
        self.ensure_main(time_out=180)
        self.log_info(self.tr('Training guide: start'))

        max_runs = int(self.config.get('每行刷取次数上限', 10) or 0)
        processed = 0
        no_progress = 0

        while True:
            if not self._open_material_panel():
                self.log_error(self.tr('Cannot open 素材获取 panel; stopping'))
                break

            rows = self._read_challenge_rows()
            self.log_info(self.tr('Found {count} challengeable rows').format(count=len(rows)))
            if not rows:
                break

            handled_this_round = 0
            for row in rows:
                if Config.MAX_ROWS and processed >= Config.MAX_ROWS:
                    self.log_info(self.tr('Reached max rows cap'))
                    break
                have, need = row['have'], row['need']
                if have >= need:
                    continue
                shortage = need - have
                self.log_info(self.tr('Row: have {have}/{need}, shortage {shortage}').format(
                    have=have, need=need, shortage=shortage))

                ok = self._farm_row(row, max_runs)
                if ok:
                    handled_this_round += 1
                    processed += 1
                else:
                    self.log_warning(self.tr('Row failed; will retry next round if possible'))

            if handled_this_round == 0:
                no_progress += 1
                if no_progress >= 2:
                    self.log_info(self.tr('No progress for 2 rounds; stopping'))
                    break
            else:
                no_progress = 0

        self.log_info(self.tr('Training guide: done, processed {count} rows').format(count=processed))
        self.info_set('training_rows_processed', processed)

    # -------------------------------------------------------------- panel
    def _open_material_panel(self):
        """Open the 素材获取 panel and land on the 培养目标/所需材料 page."""
        for step in Config.OPEN_PANEL_STEPS:
            kind = step[0]
            try:
                if kind == 'ocr':
                    text = step[1]
                    boxes = self.ocr(match=re.compile(text))
                    if boxes:
                        self.click_box(boxes[0], after_sleep=Config.WAIT_PANEL_TIMEOUT)
                        if self._on_material_page():
                            return True
                elif kind == 'key':
                    key = self.key_config.get(step[1])
                    if key:
                        self.send_key(key, after_sleep=Config.WAIT_PANEL_TIMEOUT)
                        if self._on_material_page():
                            return True
                elif kind == 'click':
                    self.click_relative(step[1], step[2], after_sleep=Config.WAIT_PANEL_TIMEOUT)
                    if self._on_material_page():
                        return True
            except Exception as exc:  # noqa: BLE001 - diagnostic path
                self.log_warning(self.tr('Open step {step} failed: {error}').format(step=step, error=exc))

        self.screenshot('training_panel_open_failed')
        return False

    def _on_material_page(self):
        """Heuristic: page is ready if we can see a 直接挑战 button or 所需材料 text."""
        for text in ('所需材料', '培养目标') + Config.CHALLENGE_TEXTS:
            if self.ocr(match=re.compile(text)):
                return True
        return False

    # -------------------------------------------------------------- read
    def _read_challenge_rows(self):
        """Return rows that have both a 直接挑战 button and a have/need number."""
        x1, y1, x2, y2 = Config.ROWS_REGION
        try:
            all_boxes = self.ocr(x1, y1, x2, y2)
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('OCR of material rows failed: {error}').format(error=exc))
            self.screenshot('training_rows_ocr_failed')
            return []

        challenge_boxes = []
        number_boxes = []
        for box in all_boxes:
            text = (box.name or '').strip()
            if any(token in text for token in Config.CHALLENGE_TEXTS):
                challenge_boxes.append(box)
            match = NUMBER_RE.search(text)
            if match:
                number_boxes.append((box, int(match.group(1)), int(match.group(2))))

        rows = []
        for cbox in challenge_boxes:
            cy = cbox.center()[1]
            best = None
            for nbox, have, need in number_boxes:
                ny = nbox.center()[1]
                if abs(ny - cy) <= Config.SAME_ROW_TOLERANCE:
                    if best is None or abs(ny - cy) < best[0]:
                        best = (abs(ny - cy), nbox, have, need)
            if best is not None:
                rows.append({'button': cbox, 'have': best[2], 'need': best[3]})

        rows.sort(key=lambda r: r['button'].center()[1])
        return rows

    # -------------------------------------------------------------- farm
    def _farm_row(self, row, max_runs):
        """Click the row's 直接挑战, fight once, exit; repeat while shortage stands."""
        have, need = row['have'], row['need']
        runs = 0
        while have < need:
            if max_runs and runs >= max_runs:
                self.log_info(self.tr('Row reached run cap {cap}').format(cap=max_runs))
                break
            if not self._click_challenge(row['button']):
                return runs > 0
            if not self._wait_and_fight():
                self.screenshot('training_fight_failed')
                return runs > 0
            if not self._exit_domain():
                self.screenshot('training_exit_failed')
                return runs > 0
            runs += 1
            # reopen panel to refresh numbers for this row
            if not self._open_material_panel():
                return runs > 0
            refreshed = self._find_row_by_position(row['button'])
            if refreshed is None:
                # row changed/disappeared; treat as handled
                break
            have, need = refreshed['have'], refreshed['need']
            row = refreshed
        return runs > 0

    def _click_challenge(self, box):
        try:
            self.click_box(box, after_sleep=Config.AFTER_CLICK_SLEEP)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('Click challenge failed: {error}').format(error=exc))
            return False

    def _wait_and_fight(self):
        """Wait for team/world after entering, then run one combat."""
        try:
            self.wait_in_team_and_world(time_out=Config.WAIT_COMBAT_TIMEOUT)
            self.combat_once(wait_combat_time=Config.COMBAT_TIMEOUT, raise_if_not_found=False)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('Combat failed: {error}').format(error=exc))
            return False

    def _exit_domain(self):
        """Return to the open world from whatever domain we entered."""
        try:
            self.make_sure_in_world()
            self.sleep(Config.AFTER_EXIT_SLEEP)
            return self.in_team_and_world()
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('Exit domain failed: {error}').format(error=exc))
            return False

    def _find_row_by_position(self, old_button):
        """After reopening, find the row closest to a previously clicked position."""
        rows = self._read_challenge_rows()
        if not rows:
            return None
        old_y = old_button.center()[1]
        return min(rows, key=lambda r: abs(r['button'].center()[1] - old_y))
