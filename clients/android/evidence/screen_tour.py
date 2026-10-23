"""Drive the installed MOSAIQ Floor debug app and capture every screen (#445).

The screenshots of docs/evidence/445-android-screens.md come from this tour. It
drives the real app, as installed, through uiautomator dumps and adb input,
and saves each screen half-size into the given directory.

Prerequisites:
- the local stack with the seed, its gateway on the host's 127.0.0.1:8080, which
  the emulator reaches as 10.0.2.2:8080;
- one emulator attached with a standard system image (an ATD image renders black);
- the debug APK installed without -g.

    ADB=adb uv run --with pillow python \
        clients/android/evidence/screen_tour.py <out_dir> all

ADB names the adb binary, and ADB_CWD the directory it runs in. Steps can also be
named one by one, for example s01-sign-in s02-refused, and they run in order
against whatever state the device is in. "all" starts from a cleared app.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ADB = os.environ.get("ADB", "adb")
ADB_CWD = os.environ.get("ADB_CWD") or None
PKG = "online.maxthecoder.mosaiq.floor"
CONTROLLER = "com.android.permissioncontroller"
OUT = Path(sys.argv[1])


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
        if scroll and swipes < 8:
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


def swipe_up() -> None:
    adb("shell", "input", "swipe", "540", "1500", "540", "700", "350")
    time.sleep(0.8)


def swipe_down() -> None:
    adb("shell", "input", "swipe", "540", "700", "540", "1500", "350")
    time.sleep(0.8)


def to_top() -> None:
    for _ in range(6):
        swipe_down()


def back() -> None:
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.8)


def hide_keyboard() -> None:
    if "mInputShown=true" in adb("shell", "dumpsys", "input_method"):
        back()


def shot(name: str) -> None:
    hide_keyboard()
    time.sleep(0.6)
    png = adb("exec-out", "screencap", "-p", binary=True)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize((image.width // 2, image.height // 2), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"445-android-{name}.png"
    image.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
        path, optimize=True
    )
    print(f"  saved {path.name} ({path.stat().st_size // 1024} KB)")


def deny_dialog() -> None:
    deadline = time.monotonic() + 15
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
                return
        time.sleep(1)
    raise AssertionError("no permission dialog to refuse")


# --- steps ----------------------------------------------------------------------


def fresh() -> None:
    """A clean app: no data, no runtime permission, launched."""
    adb("shell", "pm", "clear", PKG)
    for p in ("CAMERA", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION"):
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
    fresh()
    shot("session-01-sign-in")


@step
def s02_refused() -> None:
    sign_in("user3@mosaiq-demo.com", "wrong-password")
    find("Code:", contains=True, timeout=20)
    shot("session-02-wrong-password")


@step
def s03_role() -> None:
    sign_in("user14@mosaiq-demo.com", "Password123!")
    time.sleep(4)
    print("  on screen:", texts()[:25])
    shot("session-03-analyst-refused")


@step
def s04_signed_in() -> None:
    sign_in("user3@mosaiq-demo.com", "Password123!")
    find("Store & check-in", timeout=30)
    time.sleep(2)
    shot("session-04-signed-in-store-picker")


@step
def s05_store() -> None:
    tap("Store 1 · Monterrey, Nuevo León")
    find("Check in", timeout=15)
    shot("checkin-05-store-chosen")


@step
def s06_location_dialog() -> None:
    tap("Check in")
    find(rid=f"{CONTROLLER}:id/permission_deny_button", timeout=15)
    shot("checkin-06-location-dialog")


@step
def s07_location_denied() -> None:
    deny_dialog()
    find("Location is off for this app", contains=True, timeout=15)
    time.sleep(
        6
    )  # the worker sends the check-in; the server's answer replaces "queued"
    print("  on screen:", texts()[:20])
    shot("checkin-07-location-denied")


@step
def s08_scan() -> None:
    tap("Scan a product")
    find("Scan", timeout=15)
    shot("scan-08-before-asking")


@step
def s09_camera_dialog() -> None:
    tap("Scan")
    find(rid=f"{CONTROLLER}:id/permission_deny_button", timeout=15)
    shot("scan-09-camera-dialog")


@step
def s10_camera_denied() -> None:
    deny_dialog()
    find("Product id", timeout=15)
    print("  on screen:", texts()[:20])
    shot("scan-10-camera-denied")


@step
def s11_manual_lookup() -> None:
    type_into(0, "2")
    hide_keyboard()
    tap("Look up")
    find("in stock at Store 1", contains=True, timeout=20)
    shot("scan-11-manual-lookup")


@step
def s12_customer_search() -> None:
    tap("Add to sale")
    tap("Customer & sale")
    find("Search customers", timeout=15)
    type_into(0, "Demo Customer 3")
    hide_keyboard()
    tap("Search customers")
    find("Demo Customer 3", contains=True, index=1, timeout=20)
    shot("sale-12-customer-search")


@step
def s13_registration_form() -> None:
    tap("Register customer")
    find("Hide registration", timeout=10)
    swipe_up()
    shot("sale-13-registration-form")


@step
def s14_register() -> None:
    type_into(0, "Tour Customer")
    type_into(1, "tour.customer@mosaiq-demo.com")
    hide_keyboard()
    tap("physical_store")
    tap("Register online", scroll=True)
    find("Registered Tour Customer.", timeout=20, scroll=True)
    shot("sale-14-registered")


@step
def s15_select_customer() -> None:
    to_top()
    tap("Hide registration")
    tap("Demo Customer 3 · customer3@mosaiq-demo.com", timeout=10)
    find("Selected: Demo Customer 3", contains=True, timeout=15)
    time.sleep(2)
    shot("sale-15-customer-selected")


@step
def s16_interests() -> None:
    x, y = find("Milk", scroll=True)["center"]
    adb("shell", "input", "tap", "127", str(y + 33))
    time.sleep(0.8)
    tap("Save interests", scroll=True)
    find("Interests saved.", timeout=20, scroll=True)
    shot("sale-16-interests-saved")


@step
def s17_insights() -> None:
    for _ in range(6):
        if "Segment" in texts():
            break
        adb("shell", "input", "swipe", "540", "900", "540", "1300", "300")
        time.sleep(0.8)
    # The section's title near the top, so the segment and the list both show.
    y = find("Segment")["center"][1]
    if y > 520:
        adb("shell", "input", "swipe", "540", str(y), "540", "480", "400")
        time.sleep(0.8)
    print("  on screen:", texts()[5:30])
    shot("insights-17-segment-and-recommendations")


@step
def s18_sale_lines() -> None:
    find("Confirm and queue sale", scroll=True, timeout=20)
    swipe_up()
    find("Demo Product 2 · 22.50 × 1", timeout=10)
    shot("sale-18-lines-before-confirm")


@step
def s19_sale_recorded() -> None:
    tap("Confirm and queue sale", scroll=True)
    find(
        "Sale saved on this phone. Its progress appears below.", timeout=15, scroll=True
    )
    time.sleep(8)
    swipe_up()
    print("  on screen:", texts()[5:30])
    shot("sale-19-recorded")


@step
def s20_refused_sale() -> None:
    tap("Start another sale", scroll=True)
    x, y = find("Product", cls=None, timeout=10, scroll=True)["center"]
    field = find(cls="android.widget.EditText", index=-1)
    adb("shell", "input", "tap", *map(str, field["center"]))
    adb("shell", "input", "text", "Demo%sProduct%s6")
    hide_keyboard()
    tap("Search products", index=-1)
    tap("Add Demo Product 6", contains=True, timeout=20, scroll=True)
    tap("Confirm and queue sale", scroll=True)
    find(
        "Sale saved on this phone. Its progress appears below.", timeout=15, scroll=True
    )
    time.sleep(8)
    swipe_up()
    print("  on screen:", texts()[5:40])
    shot("sale-20-refused")


@step
def s21_outbox_refused() -> None:
    to_top()
    adb(
        "shell", "input", "swipe", "900", "358", "200", "358", "300"
    )  # the tab row scrolls sideways
    time.sleep(0.8)
    tap("Outbox")
    time.sleep(2)
    print("  on screen:", texts()[5:30])
    shot("outbox-21-refused-sale")


GATEWAY = "http://127.0.0.1:8080"
CUSTOMER_3 = "00000000-0000-0000-0000-000000000003"


def reco_clicks() -> int:
    """RECO_CLICK events of customer 3, read as the seeded analyst."""
    import json
    import urllib.request

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
            "password": "Password123!",
            "device_label": "screen tour",
        }
    ).encode()
    token = call("POST", "/api/auth/v1/sessions", login)["access_token"]
    page = call(
        "GET",
        f"/api/engagement/v1/engagement-events?customer_id={CUSTOMER_3}&type=RECO_CLICK&size=100",
        token=token,
    )
    return len([e for e in page["engagement_events"] if not e.get("voided")])


@step
def s22_reco_click() -> None:
    before = reco_clicks()
    tap("Customer & sale")
    to_top()
    # Starting another sale clears the customer: pick customer 3 again.
    tap("Demo Customer 3 · customer3@mosaiq-demo.com", timeout=15, scroll=True)
    find("Selected: Demo Customer 3", contains=True, timeout=15, scroll=True)
    time.sleep(3)  # the segment and recommendations load after the selection
    first = None
    for _ in range(14):
        first = next((t for t in texts() if t.startswith("1. ")), None)
        if first:
            break
        adb("shell", "input", "swipe", "540", "1500", "540", "1050", "300")
        time.sleep(0.6)
    assert first, "no recommendation is shown"
    print("  tapping", first)
    shot("insights-22a-before-the-tap")
    tap(first, timeout=10)
    time.sleep(3)
    print("  on screen:", texts()[5:20])
    shot("insights-22b-recommendation-opened")
    time.sleep(8)
    after = reco_clicks()
    time.sleep(20)
    later = reco_clicks()
    print(
        f"  RECO_CLICK for customer 3: before {before}, after {after},"
        f" 20 s later {later}"
    )


def tabs_to(name: str) -> None:
    """The tab row scrolls sideways: bring the named tab into view, then tap it."""
    for direction in (("900", "200"), ("200", "900")):
        if name in texts():
            break
        adb("shell", "input", "swipe", direction[0], "358", direction[1], "358", "300")
        time.sleep(0.8)
    tap(name)


@step
def s23_outbox_empty() -> None:
    tabs_to("Outbox")
    tap("Dismiss", timeout=10)
    find("Nothing is waiting, and nothing was refused.", timeout=10)
    shot("outbox-23-empty")


@step
def s24_queued_offline() -> None:
    adb("shell", "cmd", "connectivity", "airplane-mode", "enable")
    time.sleep(3)
    if not any(n["pkg"].endswith("permissioncontroller") for n in nodes()):
        if "Check in" not in texts():
            tabs_to("Store & check-in")
        tap("Check in")
    deny_dialog()  # asked again: one refusal is not yet "don't ask again"
    find("Check-in queued. It is sent as soon as the network allows.", timeout=20)
    shot("checkin-24-queued-offline")


@step
def s25_sign_out_warning() -> None:
    tabs_to("Sign out")
    find("Writes are still waiting", timeout=10)
    shot("session-25-sign-out-warning")
    tap("Stay signed in")
    adb("shell", "cmd", "connectivity", "airplane-mode", "disable")
    time.sleep(12)
    print(
        "  after reconnecting:",
        [t for t in texts() if "check-in" in t.lower() or "verified" in t.lower()][:4],
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
    for name in names:
        if name == "dump":
            dump()
            continue
        print(f"== {name}")
        STEPS[name]()
