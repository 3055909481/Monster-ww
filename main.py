if __name__ == '__main__':
    from src.identity import apply as apply_identity

    apply_identity()

    from config import config
    from Mon import OK

    config = config
    ok = OK(config)
    ok.start()
