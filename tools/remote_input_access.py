#!/usr/bin/python3
"""One-time system setup: active-seat ACL for Xiaomi RC003 input only.

Run with pkexec or sudo. This never adds the user to the broad input group,
changes other keyboards, installs a privileged daemon, or makes devices public.
"""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RULE_FILE = Path("/etc/udev/rules.d/70-sayrelay-remote.rules")
RULE = ('# SayRelay: Xiaomi RC003 remote only; active desktop user via logind.\n'
        'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{id/bustype}=="0005", '
        'ATTRS{id/vendor}=="2717", ATTRS{id/product}=="32b8", TAG+="uaccess"\n')


def nodes():
    for node in Path("/sys/class/input").glob("event*"):
        try:
            ids = tuple((node / "device/id" / key).read_text().strip().lower()
                        for key in ("bustype", "vendor", "product"))
        except OSError:
            continue
        if ids == ("0005", "2717", "32b8"):
            yield node


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Remove SayRelay's rule; reconnect remote to revoke existing ACL")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Requires system authentication: use pkexec or sudo")
    if RULE_FILE.is_symlink() or (RULE_FILE.exists() and RULE_FILE.read_text() != RULE):
        raise RuntimeError("Existing rule differs; refusing to replace it")
    if args.remove:
        RULE_FILE.unlink(missing_ok=True)
    else:
        RULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".sayrelay-", dir=RULE_FILE.parent)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(RULE)
                output.flush()
                os.fchmod(output.fileno(), 0o644)
            os.replace(name, RULE_FILE)
        finally:
            Path(name).unlink(missing_ok=True)
    subprocess.run(["/usr/bin/udevadm", "control", "--reload-rules"], check=True, timeout=10)
    for node in nodes():
        subprocess.run(["/usr/bin/udevadm", "trigger", "--action=change", str(node.resolve())],
                       check=True, timeout=10)
    subprocess.run(["/usr/bin/udevadm", "settle", "--timeout=5"], check=True, timeout=8)
    print("SayRelay remote access rule removed; reconnect the remote to revoke existing access." if args.remove else
          "SayRelay: Xiaomi remote access enabled for the active desktop user.")


if __name__ == "__main__":
    main()
