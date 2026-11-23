"""Drive the installed MOSAIQ Floor debug app and capture every screen (#445, #733).

The screenshots of docs/evidence/733-android-screens-375dp.md come from this
tour. It drives the real app, as installed, through uiautomator dumps and adb
input, at 375 x 667 dp, and saves each screen in light and in dark, full size,
into the given directory. #445 ran the first version of this tour before the
MOSAIQ refresh (#716); its screenshots are the "before" of #733.

Prerequisites:
- the local stack with the seed, its gateway on the host's 127.0.0.1:8080, which
  the emulator reaches as 10.0.2.2:8080;
- one emulator attached with a standard system image (an ATD image renders black);
- the debug APK installed without -g.

    ADB=adb uv run --with pillow python \
        clients/android/evidence/screen_tour.py <out_dir> all

ADB names the adb binary, and ADB_CWD the directory it runs in. Steps can also be
named one by one, for example s01-sign-in s02-refused, and they run in order
against whatever state the device is in. "all" starts from a cleared app. The
display is set to 750 x 1334 px at 320 dpi, which is 375 x 667 dp, and put back
to the device's own size and the light scheme at the end.

profile_outbox_tour.py reuses these helpers.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ADB = os.environ.get("ADB", "adb")
ADB_CWD = os.environ.get("ADB_CWD") or None
PKG = "online.maxthecoder.mosaiq.floor"
CONTROLLER = "com.android.permissioncontroller"
OUT = Path(sys.argv[1])
PREFIX = "733-android"
PASSWORD = "Password123!"  # the seed's published demonstration password (ADR-0008)
# A new customer per run, so the tour can run again against the same database.
TOUR_EMAIL = f"tour.customer.{int(time.time())}@mosaiq-demo.com"

# At 375 x 667 dp the content sits between the top bar (y < 200) and the bottom
# navigation (y > 1126), and the sale screen's sticky total starts at y 716 to
# 880. Every vertical gesture stays inside y 250..700, so it always lands on
# the scrolling content, never on a bar.
TOP, BOTTOM = 250, 700


def adb(*args: str, binary: bool = False, check: bool = True):
    done = subprocess.run([ADB, *args], capture_output=True, check=check, cwd=ADB_CWD)
    return (
        done.stdout
        if binary
        else done.stdout.decode(errors="replace").replace("\r", "")
    )


def nodes() -> list[dict]:
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    raw = adb("exec-out", "cat", "/sdcard/ui.xml", binary=True)
    root = ET.fromstring(raw[raw.index(b"<") :])
    found = []
    for n in root.iter("node"):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds", "[0,0][0,0]")))
        found.append(
            {
                "text": n.get("text", ""),
                "desc": n.get("content-desc", ""),
                "id": n.get("resource-id", ""),
                "cls": n.get("class", ""),
                "pkg": n.get("package", ""),
                "click": n.get("clickable") == "true",
                "checkable": n.get("checkable") == "true",
                "checked": n.get("checked") == "true",
                "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                "bounds": (x1, y1, x2, y2),
            }
        )
    return found


def texts() -> list[str]:
    return [n["text"] or n["desc"] for n in nodes() if n["text"] or n["desc"]]


def find(
    text: str | None = None,
    *,
    contains: bool = False,
    rid: str | None = None,
    cls: str | None = None,
    index: int = 0,
    timeout: float = 15,
    scroll: bool = False,
):
    deadline = time.monotonic() + timeout
    swipes = 0
    while True:
        hits = []
        for n in nodes():
            label = n["text"] or n["desc"]
            if rid and n["id"] != rid:
                continue
            if cls and n["cls"] != cls:
                continue
            if text is not None and not (text in label if contains else label == text):
                continue
            hits.append(n)
        if hits and (index < 0 or len(hits) > index):
            return hits[index]
        if time.monotonic() > deadline:
            raise AssertionError(
                f"not found: {text or rid or cls!r}; on screen: {texts()[:40]}"
            )
        if scroll and swipes < 12:
            swipe_up()
            swipes += 1
        else:
            time.sleep(1)


def tap(text: str | None = None, **kw) -> None:
    x, y = find(text, **kw)["center"]
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(0.8)


def type_into(field_index: int, value: str) -> None:
    x, y = find(cls="android.widget.EditText", index=field_index)["center"]
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(0.4)
    adb("shell", "input", "text", value.replace(" ", "%s"))
    time.sleep(0.4)


def clear_field(field_index: int) -> None:
    x, y = find(cls="android.widget.EditText", index=field_index)["center"]
    adb("shell", "input", "tap", str(x), str(y))
    adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
    adb("shell", "input", "keyevent", *(["KEYCODE_DEL"] * 40))


def drag(from_y: int, to_y: int, ms: int = 1200) -> None:
    """A slow drag: the content follows the finger and does not fling."""
    adb("shell", "input", "swipe", "375", str(from_y), "375", str(to_y), str(ms))
    time.sleep(0.8)


def swipe_up() -> None:
    drag(BOTTOM, TOP, 350)


def swipe_down() -> None:
    drag(TOP, BOTTOM, 350)


def to_top() -> None:
    for _ in range(8):
        swipe_down()


def locate(text: str, **kw) -> dict:
    """`text` on screen, looked for above the screen first, then below it."""
    for move in [None] + [swipe_down] * 8 + [swipe_up] * 16:
        if move:
            move()
        try:
            return find(text, timeout=0, **kw)
        except AssertionError:
            pass
    return find(text, timeout=0, **kw)


def bring(text: str, y: int = 300, **kw) -> None:
    """Scroll until `text`, above, on or below the screen, sits near `y`."""
    for _ in range(8):
        at = locate(text, **kw)["center"][1]
        step = max(-(BOTTOM - TOP), min(BOTTOM - TOP, at - y))
        if abs(step) < 40:
            return
        start = BOTTOM if step > 0 else TOP
        drag(start, start - step)


def back() -> None:
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.8)


def hide_keyboard() -> None:
    if "mInputShown=true" in adb("shell", "dumpsys", "input_method"):
        back()


def viewport(on: bool) -> None:
    if on:
        adb("shell", "wm", "size", "750x1334")
        adb("shell", "wm", "density", "320")
    else:
        adb("shell", "wm", "size", "reset")
        adb("shell", "wm", "density", "reset")
    time.sleep(1)


def scheme(dark: bool) -> None:
    adb("shell", "cmd", "uimode", "night", "yes" if dark else "no")
    time.sleep(2.5)


def airplane(on: bool) -> None:
    adb("shell", "cmd", "connectivity", "airplane-mode", "enable" if on else "disable")
    time.sleep(3)


def shot(name: str) -> None:
    hide_keyboard()
    time.sleep(0.6)
    png = adb("exec-out", "screencap", "-p", binary=True)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert image.size == (750, 1334), image.size
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{PREFIX}-{name}.png"
    image.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
        path, optimize=True
    )
    print(f"  saved {path.name} ({path.stat().st_size // 1024} KB)")


def both(name: str, ready: str | None = None, frame=None, **kw) -> None:
    """The screen in light, then in dark, waiting for `ready` after each switch.

    The activity is recreated on each switch. The app keeps its state, but a
    scrolled screen can come back at another offset while its parts reload, so
    `frame`, when given, scrolls it into place again before each capture.
    """
    for dark in (False, True):
        scheme(dark)
        if frame:
            time.sleep(1)  # the recreated screen settles before it is scrolled
            frame()
        find(ready, timeout=20, **kw)
        shot(f"{name}-{'dark' if dark else 'light'}")
    scheme(False)
    if frame:
        frame()
    find(ready, timeout=20, **kw)


def deny_dialog(timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for n in nodes():
            if n["id"] in (
                f"{CONTROLLER}:id/permission_deny_button",
                f"{CONTROLLER}:id/permission_deny_and_dont_ask_again_button",
            ) or (
                n["pkg"].endswith("permissioncontroller")
                and n["text"] in ("Don’t allow", "Don't allow")
            ):
                adb("shell", "input", "tap", *map(str, n["center"]))
                time.sleep(1)
                return True
        time.sleep(1)
    if timeout >= 15:
        raise AssertionError("no permission dialog to refuse")
    return False


# --- steps ----------------------------------------------------------------------

RUNTIME_PERMISSIONS = (
    "CAMERA",
    "ACCESS_FINE_LOCATION",
    "ACCESS_COARSE_LOCATION",
    "POST_NOTIFICATIONS",
)


def fresh() -> None:
    """A clean app: no data, no runtime permission, launched."""
    adb("shell", "pm", "clear", PKG)
    for p in RUNTIME_PERMISSIONS:
        adb("shell", "pm", "revoke", PKG, f"android.permission.{p}", check=False)
        adb(
            "shell",
            "pm",
            "clear-permission-flags",
            PKG,
            f"android.permission.{p}",
            "user-set",
            "user-fixed",
            check=False,
        )
    adb("shell", "am", "start", "-W", "-n", f"{PKG}/.MainActivity")
    find("Sign in", timeout=30)


def sign_in(email: str, password: str) -> None:
    clear_field(0)
    type_into(0, email)
    clear_field(1)
    type_into(1, password)
    hide_keyboard()
    tap("Sign in", index=-1)  # the button, below the title


STEPS = {}


def step(fn):
    STEPS[fn.__name__.replace("_", "-")] = fn
    return fn


@step
def s01_sign_in() -> None:
    scheme(False)
    fresh()
    both("session-01-sign-in", "Sign in with your store account to start the shift.")


@step
def s02_refused() -> None:
    sign_in("user3@mosaiq-demo.com", "wrong-password")
    both("session-02-wrong-password", "That email or password", contains=True)


@step
def s03_role() -> None:
    sign_in("user14@mosaiq-demo.com", PASSWORD)
    both("session-03-analyst-refused", "You were signed out.", contains=True)


@step
def s04_signed_in() -> None:
    sign_in("user3@mosaiq-demo.com", PASSWORD)
    find("Account menu", timeout=30)
    time.sleep(2)
    both("session-04a-signed-in", "Choose the store you are working in to check in.")
    tap("Pick a store", index=1)  # the button; the top bar's store chip is another
    both("session-04b-store-picker", "Page 1 of", contains=True)


@step
def s05_store() -> None:
    tap("Store 1")
    both("checkin-05-store-chosen", "Not checked in")


@step
def s06_location_dialog() -> None:
    tap("Check in here")
    both("checkin-06-location-dialog", rid=f"{CONTROLLER}:id/permission_deny_button")


@step
def s07_location_denied() -> None:
    deny_dialog()
    # check-ins-service's answer replaces "Waiting to send".
    find("No location was sent", contains=True, timeout=30)
    both("checkin-07-location-denied", "not verified", contains=True)


@step
def s08_scan() -> None:
    tap("Scan", index=-1)  # the bottom navigation
    both(
        "scan-08-before-asking",
        "Open the camera to read a product QR code or barcode.",
    )


@step
def s09_camera_dialog() -> None:
    tap("Scan", index=0)  # the screen's own button, above the navigation
    both("scan-09-camera-dialog", rid=f"{CONTROLLER}:id/permission_deny_button")


@step
def s10_camera_denied() -> None:
    deny_dialog()
    both("scan-10-camera-denied", "The camera is off for this app")


@step
def s11_manual_lookup() -> None:
    bring("Product number", 500)
    clear_field(0)
    type_into(0, "2")
    hide_keyboard()
    tap("Look up", scroll=True)
    find("units at Store 1", contains=True, timeout=20, scroll=True)
    both(
        "scan-11-manual-lookup",
        "units at Store 1",
        frame=lambda: bring("Demo Product 2", 400),
        contains=True,
    )


@step
def s12_customer_search() -> None:
    tap("Add to sale")
    tap("Sale", index=-1)
    find("Search customers", timeout=15)
    type_into(0, "Demo Customer 3")
    hide_keyboard()
    tap("Search customers")
    find("customer3@mosaiq-demo.com", timeout=20, scroll=True)
    both(
        "sale-12-customer-search",
        "customer3@mosaiq-demo.com",
        frame=lambda: bring("Search customers", 290),
    )


@step
def s13_registration_form() -> None:
    tap("New customer")
    find("Registration channel", timeout=10)
    both("sale-13-registration-form", "Registration channel")


@step
def s14_register() -> None:
    type_into(0, "Tour Customer")
    type_into(1, TOUR_EMAIL)
    hide_keyboard()
    tap("In store", scroll=True)
    tap("Register customer", scroll=True)
    # The sale screen comes back below the customer, at the new segment part.
    find("Segment", timeout=20)
    both("sale-14-registered", TOUR_EMAIL, frame=to_top)


@step
def s15_select_customer() -> None:
    tap("Change")
    tap("Search customers")
    # The row, by its email: the search field also reads "Demo Customer 3".
    tap("customer3@mosaiq-demo.com", timeout=20, scroll=True)
    find("Champion", timeout=20, scroll=True)
    both("sale-15-customer-selected", "customer3@mosaiq-demo.com", frame=to_top)


def checkbox_of(label: str) -> dict:
    """The checkable row that holds `label`, which must be on screen."""
    y = find(label)["center"][1]
    for n in nodes():
        x1, y1, x2, y2 = n["bounds"]
        if n["checkable"] and y1 <= y <= y2:
            return n
    raise AssertionError(f"no checkbox for {label!r}")


@step
def s16_interests() -> None:
    to_top()
    if "Show interests" in texts():
        tap("Show interests")
    bring("Milk", 450)
    if not checkbox_of("Milk")["checked"]:
        tap("Milk")
    assert checkbox_of("Milk")["checked"], "Milk did not tick"
    tap("Save interests", scroll=True)
    find("Interests saved.", timeout=20, scroll=True)
    both(
        "sale-16-interests-saved",
        "Interests saved.",
        frame=lambda: bring("Interests saved.", 520),
    )


@step
def s17_insights() -> None:
    to_top()
    tap("Hide interests")
    find("Recommendations", timeout=20, scroll=True)
    both(
        "insights-17-segment-and-recommendations",
        "1. ",
        frame=lambda: bring("Segment", 260),
        contains=True,
    )


@step
def s18_sale_lines() -> None:
    both(
        "sale-18a-lines-before-confirm",
        "$22.50 each",
        # The cart line; a recommendation reads "1. Demo Product 2".
        frame=lambda: bring("Demo Product 2", 450),
    )
    tap("Record sale")
    both("sale-18b-check-the-sale", "Check the sale")


@step
def s19_sale_recorded() -> None:
    tap("Confirm sale")
    recorded = find(r"recorded.", contains=True, timeout=30)["text"]
    print(" ", recorded)
    find("Sent", timeout=30, scroll=True)
    both("sale-19-recorded", "Sent", frame=lambda: bring("Sales on this phone", 300))


@step
def s20_refused_sale() -> None:
    tap("Start another sale")
    bring("Search products", 400)
    type_into(0, "Demo Product 6")
    hide_keyboard()
    tap("Search")
    tap("Add Demo Product 6", timeout=20, scroll=True)
    tap("Record sale")
    tap("Confirm sale")
    # A refusal raises a notification, and Android asks once whether it may.
    deny_dialog(timeout=12)
    find("The sale was not recorded", timeout=30)
    both(
        "sale-20-refused",
        "Needs attention",
        frame=lambda: bring("Sales on this phone", 270),
    )


@step
def s21_outbox_refused() -> None:
    tap("Outbox", index=-1)
    both("outbox-21-refused-sale", "1 needs attention")


GATEWAY = "http://127.0.0.1:8080"
CUSTOMER_3 = "00000000-0000-0000-0000-000000000003"


def reco_clicks() -> int:
    """RECO_CLICK events of customer 3 on engagement v2, read as the seeded analyst."""

    def call(method, path, body=None, token=None):
        request = urllib.request.Request(GATEWAY + path, method=method, data=body)
        request.add_header("Accept", "application/json")
        if body:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=10) as reply:
            return json.load(reply)

    login = json.dumps(
        {
            "email": "user14@mosaiq-demo.com",
            "password": PASSWORD,
            "device_label": "screen tour",
        }
    ).encode()
    token = call("POST", "/api/auth/v1/sessions", login)["access_token"]
    page = call(
        "GET",
        f"/api/engagement/v2/engagement-events?customer_id={CUSTOMER_3}"
        "&type=RECO_CLICK&voided=false&size=100",
        token=token,
    )
    return len(page["engagement_events"])


@step
def s22_reco_click() -> None:
    before = reco_clicks()
    tap("Sale", index=-1)
    tap("Start another sale")  # the customer stays chosen
    to_top()
    find("Recommendations", timeout=20, scroll=True)
    bring("Recommendations", 300)
    first = find("1. ", contains=True, timeout=20)["text"]
    print("  tapping", first)
    both(
        "insights-22a-before-the-tap",
        first,
        frame=lambda: bring("Recommendations", 300),
    )
    tap(f"Open {first.removeprefix('1. ')} in Scan")
    find("units at Store 1", contains=True, timeout=20)
    both("insights-22b-recommendation-opened", "units at Store 1", contains=True)
    time.sleep(8)
    after = reco_clicks()
    time.sleep(20)
    later = reco_clicks()
    print(
        f"  RECO_CLICK for customer 3: before {before}, after {after},"
        f" 20 s later {later}"
    )


@step
def s23_outbox_empty() -> None:
    tap("Outbox", index=-1)
    tap("Dismiss", timeout=10)
    both("outbox-23-empty", "Everything is synced")


@step
def s24_queued_offline() -> None:
    airplane(True)
    tap("Check-in", index=-1)
    tap("Check in here")
    deny_dialog(timeout=8)  # asked again: one refusal is not yet "don't ask again"
    both("checkin-24-queued-offline", "Waiting to send")


@step
def s25_sign_out_warning() -> None:
    tap("Account menu")
    tap("Sign out")
    both("session-25-sign-out-warning", "Writes are still waiting")
    tap("Stay signed in")
    airplane(False)
    time.sleep(12)
    print(
        "  after reconnecting:",
        [t for t in texts() if "checked in" in t.lower() or "verified" in t.lower()][
            :3
        ],
    )


def dump() -> None:
    for n in nodes():
        if n["text"] or n["desc"] or n["cls"].endswith(("EditText", "CheckBox")):
            print(
                " ",
                n["cls"].split(".")[-1],
                repr(n["text"] or n["desc"]),
                n["id"],
                n["center"],
                "click" if n["click"] else "",
            )


if __name__ == "__main__":
    names = list(STEPS) if sys.argv[2:] == ["all"] else sys.argv[2:]
    viewport(True)
    try:
        for name in names:
            if name == "dump":
                dump()
                continue
            print(f"== {name}")
            STEPS[name]()
    finally:
        scheme(False)
        viewport(False)
