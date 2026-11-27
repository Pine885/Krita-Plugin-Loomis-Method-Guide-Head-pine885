import sys

def warn(msg: str) -> None:
    print("[loomis] %s" % msg, file=sys.stderr)
