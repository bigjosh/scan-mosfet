# Install the MOSFET Scanner on an Android phone

The web UI can't reach the scanner rig from **Chrome (or Chrome Dev) on
Android** — current Chrome blocks wired CDC serial devices over both WebUSB
and Web Serial. So on a phone you run a tiny **shell app** instead: it loads
the exact same web UI and adds a native USB-serial bridge that has none of
those browser restrictions. (Desktop Chrome is fine in the browser — see the
bottom of this page.)

Everything below is done **on the phone**.

## 1. Download the app

Tap this link on the phone to download the latest APK (~2 MB):

**➡️ [Download app-release.apk](https://github.com/bigjosh/scan-mosfet/releases/latest/download/app-release.apk)**

If that doesn't start a download, open the
[Releases page](https://github.com/bigjosh/scan-mosfet/releases/latest) and tap
`app-release.apk` under **Assets**. (Direct link to this exact version:
[app-v1.0.0/app-release.apk](https://github.com/bigjosh/scan-mosfet/releases/download/app-v1.0.0/app-release.apk).)

## 2. Install it

1. Open the downloaded file (from the browser's download bar, or **Files →
   Downloads → `app-release.apk`**).
2. Android will ask to **allow installing unknown apps** for whichever app you
   downloaded with (Chrome, Files, etc.). Tap through to settings, enable
   **Allow from this source**, and come back. This is a one-time prompt.
3. Tap **Install**, then **Open**.

> Updating later: just install a newer APK over the top — your scan history and
> settings survive (the app is signed with the same key).

## 3. Connect the rig and scan

1. Plug the scanner rig into the phone with a **USB-C OTG** adapter / cable
   (the phone powers and talks to the Arduino).
2. Open the **MOSFET Scanner** app and tap **Connect**.
3. Approve the **USB permission** dialog when Android shows it. (Plugging the
   rig in while the app is open usually grants this automatically.)

You're connected when the status chip at the top reads **`Android USB`**
(that's the native bridge). From here it's the same UI as the website — run a
**Scan** or the guided **Bring-up**. Exported CSVs land in the phone's
**Downloads** folder.

The app always loads the latest UI from the web, so it updates itself — you
only ever need a new APK if the USB bridge itself changes.

## Try it without hardware

Want to see the UI first? Tap **Demo** in the app (or open
[the demo in any browser](https://bigjosh.github.io/scan-mosfet/?demo)) for a
simulated rig — no phone app or Arduino needed.

## Troubleshooting

- **"App not installed" / install blocked** — make sure the download finished
  (re-download if it looks truncated), and that you enabled *Allow from this
  source* in step 2. If you previously installed a different build, uninstall it
  first.
- **"No USB serial device found"** — use a known-good **data** OTG cable (not a
  charge-only one), reseat both ends, and make sure the Arduino's power LED is
  on. Then tap **Connect** again.
- **"Could not claim the USB interfaces"** — another app grabbed the device.
  Force-stop any serial-terminal app, clear its *Open by default* in Android
  settings, replug the rig, and retry.
- **Chip says `WebUSB (CDC)` or `Web Serial`, not `Android USB`** — you opened
  the website in a browser, not the installed app. Open the **MOSFET Scanner**
  app instead.

## On a desktop instead?

No app needed. Open **<https://bigjosh.github.io/scan-mosfet/>** in desktop
Chrome or Edge, plug the Uno into the computer, and tap **Connect** — desktop
browsers support Web Serial directly.
