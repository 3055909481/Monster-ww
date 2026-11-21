if __name__ == '__main__':
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

    config = config
    ok = OK(config)
    ok.start()
