"""Pure calculator for the training-guide planner.

No framework / GUI / screenshot dependency: given a list of material demands,
it works out how many runs each domain needs, aggregates demands targeting the
same domain, and trims the plan to a stamina budget. The result can be fed
straight into RoutinePlanStore as pipeline steps.

A demand row::

    {"domain": "forgery", "locator": {"地区": "瑾珑·梦州", "武器类型": "长刃"},
     "material": "迅刃·材料", "have": 4, "need": 46, "per_run": 2}

``per_run`` defaults to the value in assets/data/training_guide.json when
omitted or <= 0.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[3] / 'assets' / 'data' / 'training_guide.json'


def load_data():
    with DATA_PATH.open(encoding='utf-8') as stream:
        return json.load(stream)


def domain_options():
    """Return [{id, name, locator, stamina, material_hint}] for the UI."""
    return load_data()['domain_types']


def domain_meta():
    data = load_data()
    return {
        entry['id']: {
            'name': entry['name'],
            'locator': list(entry['locator']),
            'stamina': entry['stamina'],
            'material_hint': entry.get('material_hint', ''),
            'default_yield': data['default_yield'].get(entry['id'], 1),
        }
        for entry in data['domain_types']
    }


@dataclass
class RunStep:
    """One planned domain run, ready to become a routine step."""

    domain: str
    locator: dict
    runs: int
    stamina: int
    materials: list = field(default_factory=list)

    def to_dict(self):
        return {
            'domain': self.domain,
            'locator': dict(self.locator),
            'runs': self.runs,
            'stamina': self.stamina,
            'materials': list(self.materials),
        }


def _normalize_demand(row, meta):
    """Validate one demand row; return (domain, locator, material, runs, stamina)."""
    if not isinstance(row, dict):
        raise ValueError('demand row must be a dict')
    domain = row.get('domain')
    if domain not in meta:
        raise ValueError(f'unknown domain: {domain!r}')
    info = meta[domain]

    raw_locator = row.get('locator') or {}
    if not isinstance(raw_locator, dict):
        raise ValueError('locator must be a dict')
    locator = {key: raw_locator.get(key, '') for key in info['locator']}
    if any(value in (None, '') for value in locator.values()):
        raise ValueError(f'locator incomplete for {domain}: {locator}')

    try:
        have = int(row.get('have', 0) or 0)
        need = int(row.get('need', 0) or 0)
        per_run = int(row.get('per_run', 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'invalid number in demand row: {row}') from exc

    if have < 0 or need < 0:
        raise ValueError('have/need must be non-negative')
    if per_run <= 0:
        per_run = info['default_yield']

    shortage = max(need - have, 0)
    runs = math.ceil(shortage / per_run) if shortage > 0 else 0
    return domain, locator, row.get('material', ''), runs, info['stamina']


def calculate(demands, stamina_budget=0, meta=None):
    """Plan runs from material demands.

    ``stamina_budget`` = 0 means unlimited. Positive values cap total stamina.
    Demands targeting the same domain+locator are merged (runs added up).
    Returns ``(steps, total_stamina, skipped)`` where ``skipped`` holds demand
    rows trimmed by the budget (each with its unmet run count).
    """
    if meta is None:
        meta = domain_meta()
    if not isinstance(demands, list):
        raise ValueError('demands must be a list')
    if stamina_budget < 0:
        raise ValueError('stamina_budget must be non-negative')

    merged = {}
    for index, row in enumerate(demands):
        domain, locator, material, runs, stamina = _normalize_demand(row, meta)
        if runs <= 0:
            continue
        key = (domain, tuple(sorted(locator.items())))
        if key not in merged:
            merged[key] = RunStep(domain=domain, locator=locator, runs=0, stamina=stamina)
        merged[key].runs += runs
        if material:
            merged[key].materials.append(f'{material}×{runs}')

    steps = list(merged.values())
    total = sum(step.runs * step.stamina for step in steps)

    skipped = []
    if stamina_budget > 0 and total > stamina_budget:
        remaining = stamina_budget
        kept = []
        for step in steps:
            cost = step.runs * step.stamina
            if cost <= remaining:
                kept.append(step)
                remaining -= cost
                continue
            affordable = remaining // step.stamina
            if affordable > 0:
                trimmed = RunStep(step.domain, dict(step.locator), affordable,
                                  step.stamina, list(step.materials))
                kept.append(trimmed)
                skipped.append({'domain': step.domain, 'locator': dict(step.locator),
                                'dropped_runs': step.runs - affordable})
                remaining -= affordable * step.stamina
            else:
                skipped.append({'domain': step.domain, 'locator': dict(step.locator),
                                'dropped_runs': step.runs})
        steps = kept
        total = stamina_budget - remaining

    return [step.to_dict() for step in steps], total, skipped


def to_routine_items(steps):
    """Convert planned steps into RoutinePlans item dicts (uid assigned on save)."""
    items = []
    for step in steps:
        params = dict(step['locator'])
        params['次数'] = step['runs']
        items.append({'id': step['domain'], 'enabled': True, 'params': params})
    return items
