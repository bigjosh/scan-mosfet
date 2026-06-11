# MOSFET Scanner — Android shell app

A ~300-line wrapper that exists for one reason: **Chrome on Android currently
blocks every path to a wired CDC serial device** (WebUSB fences the CDC-data
interface; wired Web Serial is still behind a non-functional flag). Native
apps use the Android USB Host API, which has no such fence.

Architecture: a fullscreen WebView loads the GitHub Pages app
(`https://bigjosh.github.io/scan-mosfet/`) and injects `window.AndroidSerial`,
a USB-serial bridge backed by
[usb-serial-for-android](https://github.com/mik3y/usb-serial-for-android)
(CDC, CH340, FTDI, CP210x — so clone boards work too). The web app's
transport layer auto-prefers the bridge when present. **All app features ship
via `git push` to /docs** — the APK only changes when the bridge does.

Extras the shell adds over the browser: native USB permission flow (plug-in
intent grants it automatically), keep-screen-on, CSV export into the system
Downloads folder, no background-tab timer throttling of the UI host.

## Install (users)

Grab the latest `app-release.apk` from
[Releases](https://github.com/bigjosh/scan-mosfet/releases) on the phone,
tap it, allow the browser to install unknown apps (one-time), Install.
Updates: install a newer APK over the top — history/settings survive
(same signing key).

## Build (maintainers)

Toolchain (no Android Studio needed): JDK 17, Android cmdline-tools with
`platforms;android-34` + `build-tools;34.0.0`, Gradle 8.7. On the bench PC
everything lives under `%LOCALAPPDATA%\android-build\` and
`local.properties` already points there.

```powershell
$env:JAVA_HOME = "$env:LOCALAPPDATA\android-build\jdk"
& "$env:LOCALAPPDATA\android-build\gradle\bin\gradle.bat" -p android assembleRelease
# -> android/app/build/outputs/apk/release/app-release.apk
```

Signing: `android/release.keystore` + `android/keystore.properties`
(both **gitignored — back them up**; losing the keystore breaks
update-in-place for installed users). Without them the build falls back
to unsigned release / use assembleDebug.

Publish: bump `versionCode`/`versionName` in `app/build.gradle`, then

```powershell
gh release create app-vX.Y.Z android/app/build/outputs/apk/release/app-release.apk
```

## Bridge protocol (JS side)

```
AndroidSerial.list()            -> '[{"id","vid","pid","name","driver"}]'
AndroidSerial.connect(id, baud) -> events via window.__androidSerialEvent
AndroidSerial.write(base64)     -> bool
AndroidSerial.close()
AndroidSerial.saveFile(name, base64)  -> system Downloads + toast
events: {type: 'connect'|'data'|'error'|'disconnect', data}  (data = base64)
```
