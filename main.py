if __name__ == '__main__':
    # Packaged updater helper: the frozen exe is itself the Python runtime, so
    # pyappify re-launches it with --pyappify-apply <plan.json> to run the
    # copied apply_update.py. Must happen before any GUI/framework import.
    import os as _os
    import runpy as _runpy
    import sys as _sys
    if '--pyappify-apply' in _sys.argv:
        _index = _sys.argv.index('--pyappify-apply')
        _plan = _sys.argv[_index + 1] if _index + 1 < len(_sys.argv) else None
        if _plan and _os.path.isfile(_plan):
            _helper = _os.path.join(_os.path.dirname(_plan), 'apply_update.py')
            _sys.argv = [str(_helper), str(_plan)]
            _runpy.run_path(str(_helper), run_name='__main__')
        _sys.exit(0)

    from src.identity import apply as apply_identity

    apply_identity()

    from config import config
    from Mon import OK

    # 定时任务弹窗增强（计划配置/执行账号）：项目层补丁，失败自动降级为原行为
    try:
        from src.schedule_patch import install as install_schedule_patch
        install_schedule_patch()
    except Exception:
        pass

    # 自适应触发间隔（空闲降占用 / 战斗保持原节奏）：失败自动降级为框架原行为
    try:
        from src.adaptive_interval import install as install_adaptive_interval
        install_adaptive_interval()
    except Exception:
        pass

    # 顶部状态栏显示「识别到的当前队伍」：失败自动降级（顶部栏保持原样）
    try:
        from src.team_overlay import install as install_team_overlay
        install_team_overlay()
    except Exception:
        pass

    # 点击坐标微抖动（提升按钮命中率）：失败自动降级为框架原行为
    try:
        from src.click_jitter import install as install_click_jitter
        install_click_jitter()
    except Exception:
        pass

    # 在线更新前弹确认框：避免静默更新，失败自动降级为框架原行为
    try:
        from src.update_confirm import install as install_update_confirm
        install_update_confirm()
    except Exception:
        pass

    config = config
    ok = OK(config)
    ok.start()
