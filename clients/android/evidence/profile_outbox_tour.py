"""Capture each store profile (#855) and the outbox (#729) at 375 dp, light and dark.

The screenshots of docs/evidence/855-729-android-375dp.md come from this tour. It
drives the installed debug app through screen_tour's helpers, against the real
services behind the gateway, and saves every screen at full size.

Prerequisites are screen_tour's: the local stack with the seed behind
127.0.0.1:8080 (10.0.2.2:8080 from the emulator), one emulator with a standard
system image, and the debug APK installed without -g. auth, users, stores and
check-ins are the services these screens call.

    ADB=adb uv run --with pillow python \
        clients/android/evidence/profile_outbox_tour.py <out_dir> [profiles] [outbox]

The display is set to 750 x 1334 px at 320 dpi, which is 375 x 667 dp, and put
back to the device's own size at the end.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screen_tour as tour  # noqa: E402  (reads <out_dir> from argv, as here)

OUT = Path(sys.argv[1])
PASSWORD = "Password123!"  # the seed's published demonstration password (ADR-0008)
# One seeded account per floor role (sql/02_seed_30_per_table.sql, F19-04).
PROFILES = (
    ("SALES_ASSOCIATE", "user31@mosaiq-demo.com"),
    ("STORE_MANAGER", "user3@mosaiq-demo.com"),
    ("LOYALTY_OFFICER", "user32@mosaiq-demo.com"),
    ("COMMERCIAL_SUPERVISOR", "user33@mosaiq-demo.com"),
    ("ADMIN", "admin@mosaiq-demo.com"),
)
# The seeded stores' locations: Store 1 is assigned to user3, Store 2 is not.
STORE_1 = ("-100.3161", "25.6866")
STORE_2 = ("-100.2597", "25.6776")


def viewport(on: bool) -> None:
    if on:
        tour.adb("shell", "wm", "size", "750x1334")
        tour.adb("shell", "wm", "density", "320")
    else:
        tour.adb("shell", "wm", "size", "reset")
        tour.adb("shell", "wm", "density", "reset")
    time.sleep(1)


def scheme(dark: bool) -> None:
    tour.adb("shell", "cmd", "uimode", "night", "yes" if dark else "no")
    time.sleep(2.5)


def airplane(on: bool) -> None:
    tour.adb(
        "shell", "cmd", "connectivity", "airplane-mode", "enable" if on else "disable"
    )
    time.sleep(3)


def at(position: tuple[str, str]) -> None:
    tour.adb("emu", "geo", "fix", *position)


def shot(name: str) -> None:
    tour.hide_keyboard()
    time.sleep(0.6)
    png = tour.adb("exec-out", "screencap", "-p", binary=True)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert image.size == (750, 1334), image.size
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    image.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
        path, optimize=True
    )
    print(f"  saved {path.name} ({path.stat().st_size // 1024} KB)")


def both(name: str, ready: str) -> None:
    """The screen in light, then in dark, waiting for `ready` after the switch."""
    scheme(False)
    tour.find(ready, timeout=15)
    shot(f"{name}-light")
    scheme(True)
    tour.find(ready, timeout=15)
    shot(f"{name}-dark")
    scheme(False)


def signed_in(email: str) -> None:
    scheme(False)
    tour.fresh()
    tour.sign_in(email, PASSWORD)
    tour.find("Account menu", timeout=30)
    time.sleep(2)


def profiles() -> None:
    """#855: each role's own bottom navigation, with the role in the account menu."""
    for role, email in PROFILES:
        print(role)
        signed_in(email)
        tour.tap("Account menu")
        tour.find(role, timeout=10)
        both(f"855-android-profile-{role.lower().replace('_', '-')}", role)


def outbox() -> None:
    """#729: empty; then a write the service refused and one waiting offline."""
    print("OUTBOX")
    signed_in("user3@mosaiq-demo.com")
    for permission in ("ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION"):
        tour.adb("shell", "pm", "grant", tour.PKG, f"android.permission.{permission}")
    tour.tap("Outbox", index=-1)
    both("729-android-outbox-01-empty", "Everything is synced")

    # check-ins refuses a store that is not the manager's: 403 forbidden_store.
    tour.tap("Check-in", index=-1)
    tour.tap("Pick a store", index=1)
    at(STORE_2)
    tour.tap("Store 2")
    tour.tap("Check in here")
    tour.find("Needs attention", timeout=30)

    # Store 1 is the manager's; with no network the check-in waits.
    tour.tap("Change store")
    tour.tap("Store 1")
    at(STORE_1)
    airplane(True)
    try:
        tour.tap("Check in here")
        tour.find("Waiting to send", timeout=30)
        tour.tap("Outbox", index=-1)
        tour.find("1 write waiting to send · 1 needs attention", timeout=15)
        both("729-android-outbox-02-refused-and-waiting", "Needs attention")
        tour.tap("Details")
        # screen_tour's swipe is for the device's own 1080 x 1920; this is 750 x 1334.
        tour.adb("shell", "input", "swipe", "375", "1000", "375", "450", "400")
        time.sleep(1)
        both("729-android-outbox-03-details-and-waiting", "Check-in at Store 1")
    finally:
        airplane(False)


if __name__ == "__main__":
    wanted = set(sys.argv[2:]) or {"profiles", "outbox"}
    viewport(True)
    try:
        if "profiles" in wanted:
            profiles()
        if "outbox" in wanted:
            outbox()
    finally:
        scheme(False)
        viewport(False)
