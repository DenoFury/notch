import faulthandler
import os
import sys

# pythonw / the packaged exe have no console: keep errors and crashes in a log so they can be diagnosed
if sys.stderr is None:
    log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Notch")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "notch.log")
    if os.path.exists(path) and os.path.getsize(path) > 512_000:
        os.replace(path, path + ".old")
    sys.stderr = sys.stdout = open(path, "a", encoding="utf-8", buffering=1)
faulthandler.enable(sys.stderr)


def _https_like_windows():
    """Check HTTPS certificates with Windows' own verifier (it fetches missing intermediate
    certificates, which Python's bundled OpenSSL does not)."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass


def _entry():
    _https_like_windows()
    args = sys.argv[1:]
    # helper modes of the packaged Notch.exe (also work from source)
    if args[:1] == ["--guard"]:
        from notch import taskbar_guard
        return taskbar_guard.main(int(args[1]))
    if args[:1] == ["--post-install"]:
        from notch import installer
        return installer.post_install(startup="--startup" in args)
    if args[:1] == ["--uninstall"]:
        from notch import installer
        return installer.uninstall(quiet="--quiet" in args)
    from notch.app import main
    return main()


if __name__ == "__main__":
    sys.exit(_entry())
