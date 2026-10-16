if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-check":
        from pathlib import Path
        from iphonebackup.packaging_check import main as packaging_check
        raise SystemExit(packaging_check(Path(sys.argv[2])))
    from iphonebackup.runtime import check_dependencies

    if not check_dependencies():
        raise SystemExit(1)
    from iphonebackup.ui import main

    raise SystemExit(main())
