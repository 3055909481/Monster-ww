if __name__ == '__main__':
    from src.identity import apply as apply_identity

    apply_identity()

    from config import config

    # 配置统一到 %APPDATA%/Monster-ww/configs：首次启动时把旧版程序目录的
    # 配置按结构核对迁移过来，并清理已废弃任务的残留配置。必须在 OK() 加载
    # 任务之前执行，且失败时静默降级（下次仍会重试）。
    try:
        from src.config_migration import install as install_config_migration
        install_config_migration()
    except Exception:
        pass

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

    # 退出时停掉所有手动任务，避免脚本残留到程序关闭之后
    try:
        from src.exit_cleanup import install as install_exit_cleanup
        install_exit_cleanup()
    except Exception:
        pass

    config = config
    ok = OK(config)
    ok.start()
