"""Semi-automatic training-guide material farmer.

Flow::

    ensure_main -> open 素材获取 -> 培养目标 -> 所需材料 page
    -> OCR each row: group text + "have/need" number + 直接挑战 button
    -> map group -> domain type (enabled by config)
    -> for enabled rows with a shortage, click 直接挑战 -> fight -> exit
    -> repeat within the configured total stamina budget

The game decides which specific domain a 直接挑战 button opens; the task only
needs the group text (to decide which category a row belongs to, and therefore
whether it is enabled) and the have/need number (to decide if there is a
shortage). Rows showing 前往 instead of 直接挑战 are skipped.

Stamina budget is a single total shared across all enabled categories. Each
fight consumes the domain's stamina cost; the plan stops when the budget is
spent or every enabled row is satisfied.

All screen-dependent constants live in ``Config`` so they can be calibrated on
a real machine.
"""

import math
import re

from Mon import Logger

from src.task.DomainTask import DomainTask
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)

NUMBER_RE = re.compile(r'(\d+)\s*/\s*(\d+)')

# Config key prefix for the three category toggles + budget
BUDGET_KEY = '总体力预算'
CATEGORY_KEYS = {
    'forgery': '补凝素领域',
    'boss_challenge': '补讨伐强敌',
    'weekly_boss': '补战歌重奏',
}

# group text (as OCR'd from the panel) -> domain type
GROUP_TO_DOMAIN = {
    '共鸣者突破材料': 'boss_challenge',
    '武器与技能素材': 'forgery',
    '技能升级材料': 'weekly_boss',
    '突破材料': 'weekly_boss',
}

# stamina cost per fight by domain type
DOMAIN_STAMINA = {
    'forgery': 40,
    'boss_challenge': 60,
    'weekly_boss': 60,
}

# expected material yield per fight (used to turn shortage into run count)
DOMAIN_YIELD = {
    'forgery': 2,
    'boss_challenge': 1,
    'weekly_boss': 1,
}


class Config:
    """Tunable screen/flow parameters (calibrate on a real machine if needed)."""

    OPEN_PANEL_STEPS = [
        ('ocr', '素材获取'),
        ('ocr', '培养目标'),
        ('key', 'Guidebook Key'),
    ]

    WAIT_PANEL_TIMEOUT = 6
    WAIT_COMBAT_TIMEOUT = 15
    COMBAT_TIMEOUT = 180

    MAX_ROWS = 20

    ROWS_REGION = (0.36, 0.12, 0.99, 0.92)

    # Vertical tolerance to pair group text / number / button on the same row.
    SAME_ROW_TOLERANCE = 0.05

    CHALLENGE_TEXTS = ('直接挑战', '挑战')

    AFTER_CLICK_SLEEP = 2
    AFTER_EXIT_SLEEP = 2


class TrainingGuideTask(DomainTask):
    """Farm enabled material categories within a total stamina budget.

    Inherits DomainTask for ``combat_once`` and ``make_sure_in_world``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '培养目标自动补齐'
        self.icon = task_icon('daily')
        self.group_name = 'Daily Activities'
        self.group_icon = task_icon('category-daily')
        self.description = 'Auto-farm material shortages within a stamina budget, by category.'
        self.support_schedule_task = True

        self.default_config[BUDGET_KEY] = 240
        self.config_description[BUDGET_KEY] = '本次运行分配的总体力；0 表示不限。每次挑战按副本类型扣对应体力。'
        self.config_type[BUDGET_KEY] = {'min': 0, 'max': 100000}

        for domain, key in CATEGORY_KEYS.items():
            # bool default -> framework auto-renders a switch; no explicit type needed
            self.default_config[key] = domain != 'weekly_boss'
            self.config_description[key] = f'是否补齐该类别的缺口（{domain}）。'

        self.add_exit_after_config()
        self.processed = 0
        self.stamina_spent = 0

    # ------------------------------------------------------------------ run
    def run(self):
        DomainTask.run(self)
        self.ensure_main(time_out=180)
        self.log_info(self.tr('Training guide: start'))

        budget = int(self.config.get(BUDGET_KEY, 0) or 0)
        enabled = self._enabled_domains()
        self.log_info(self.tr('Enabled categories: {items}; budget {budget}').format(
            items=list(enabled), budget=budget))
        if not enabled:
            self.log_info(self.tr('No category enabled; stopping'))
            return

        processed = 0
        spent = 0
        no_progress = 0

        while True:
            if budget and spent >= budget:
                self.log_info(self.tr('Stamina budget exhausted ({spent}/{budget})').format(
                    spent=spent, budget=budget))
                break

            if not self._open_material_panel():
                self.log_error(self.tr('Cannot open 素材获取 panel; stopping'))
                break

            rows = self._read_rows()
            self.log_info(self.tr('Parsed {count} rows').format(count=len(rows)))
            if not rows:
                break

            handled = 0
            for row in rows:
                domain = row['domain']
                if domain not in enabled:
                    continue
                if domain not in DOMAIN_STAMINA:
                    continue
                have, need = row['have'], row['need']
                if have >= need:
                    continue
                cost = DOMAIN_STAMINA[domain]
                if budget and spent + cost > budget:
                    continue
                shortage = need - have
                self.log_info(self.tr('{domain} row: {have}/{need}, shortage {n}, cost {cost}').format(
                    domain=domain, have=have, need=need, n=shortage, cost=cost))

                if self._farm_one(row):
                    handled += 1
                    processed += 1
                    spent += cost
                    if budget and spent >= budget:
                        break

            if handled == 0:
                no_progress += 1
                if no_progress >= 2:
                    self.log_info(self.tr('No progress for 2 rounds; stopping'))
                    break
            else:
                no_progress = 0

        self.processed = processed
        self.stamina_spent = spent
        self.log_info(self.tr('Training guide: done, {n} fights, {s} stamina').format(
            n=processed, s=spent))
        self.info_set('training_fights', processed)
        self.info_set('training_stamina_spent', spent)

    def _enabled_domains(self):
        return {domain for domain, key in CATEGORY_KEYS.items()
                if self.config.get(key, False)}

    # -------------------------------------------------------------- panel
    def _open_material_panel(self):
        for step in Config.OPEN_PANEL_STEPS:
            kind = step[0]
            try:
                if kind == 'ocr':
                    boxes = self.ocr(match=re.compile(step[1]))
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
            except Exception as exc:  # noqa: BLE001
                self.log_warning(self.tr('Open step {step} failed: {error}').format(step=step, error=exc))

        self.screenshot('training_panel_open_failed')
        return False

    def _on_material_page(self):
        for text in ('所需材料', '培养目标') + Config.CHALLENGE_TEXTS:
            if self.ocr(match=re.compile(text)):
                return True
        return False

    # -------------------------------------------------------------- read
    def _read_rows(self):
        """OCR rows into {domain, have, need, button} for challengeable rows."""
        x1, y1, x2, y2 = Config.ROWS_REGION
        try:
            boxes = self.ocr(x1, y1, x2, y2)
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('OCR of rows failed: {error}').format(error=exc))
            self.screenshot('training_rows_ocr_failed')
            return []

        groups, numbers, challenges = [], [], []
        for box in boxes:
            text = (box.name or '').strip()
            domain = self._match_group(text)
            if domain is not None:
                groups.append((box, domain))
            if any(token in text for token in Config.CHALLENGE_TEXTS):
                challenges.append(box)
            match = NUMBER_RE.search(text)
            if match:
                numbers.append((box, int(match.group(1)), int(match.group(2))))

        rows = []
        for cbox in challenges:
            cy = cbox.center()[1]
            domain = self._nearest_group(groups, cy)
            pair = self._nearest_number(numbers, cy)
            if domain is None or pair is None:
                continue
            rows.append({'button': cbox, 'domain': domain,
                         'have': pair[1], 'need': pair[2]})
        rows.sort(key=lambda r: r['button'].center()[1])
        return rows

    @staticmethod
    def _match_group(text):
        for group, domain in GROUP_TO_DOMAIN.items():
            if group in text:
                return domain
        return None

    def _nearest_group(self, groups, y):
        best = None
        for box, domain in groups:
            d = abs(box.center()[1] - y)
            if d <= Config.SAME_ROW_TOLERANCE and (best is None or d < best[0]):
                best = (d, domain)
        return best[1] if best else None

    def _nearest_number(self, numbers, y):
        best = None
        for box, have, need in numbers:
            d = abs(box.center()[1] - y)
            if d <= Config.SAME_ROW_TOLERANCE and (best is None or d < best[0]):
                best = (d, box, have, need)
        return best[1:] if best else None

    # -------------------------------------------------------------- farm
    def _farm_one(self, row):
        """Click 直接挑战, fight once, exit. One fight per budget unit."""
        try:
            self.click_box(row['button'], after_sleep=Config.AFTER_CLICK_SLEEP)
            self.wait_in_team_and_world(time_out=Config.WAIT_COMBAT_TIMEOUT)
            self.combat_once(wait_combat_time=Config.COMBAT_TIMEOUT, raise_if_not_found=False)
            self.make_sure_in_world()
            self.sleep(Config.AFTER_EXIT_SLEEP)
            return self.in_team_and_world()
        except Exception as exc:  # noqa: BLE001
            self.log_warning(self.tr('Farm one failed: {error}').format(error=exc))
            self.screenshot('training_farm_one_failed')
            return False
