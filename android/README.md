# Android client skeleton (primary product UI)

Java Android Views project, no extra UI/network libraries. Contains masked token
login, chat, sessions/history, pending confirmations with exact action details,
separate approve/execute/cancel controls, reminders and inbox. Voice is an explicit
unavailable adapter. No real Android device-control actions are implemented.

This project was NOT compiled here: JDK/Gradle/adb were not found on PATH. No SDK,
Gradle, JDK or other system software was installed. Source-level skeleton is not a
verified APK. An Android build and device acceptance tests are required.

## Manual build

1. Install Android Studio and Android SDK through your normal approved process.
2. Open this android directory as a Gradle project.
3. Select JDK 17 and install Android SDK platform 35 and build-tools 34.0.0.
   AGP is pinned to 8.7.3; use Gradle 8.9. See official compatibility documentation:
   https://developer.android.com/build/releases/agp-8-7-0-release-notes
4. No binary Gradle wrapper is bundled. With locally installed Gradle 8.9 run
   `gradle wrapper --gradle-version 8.9` from this directory, then
   `.\gradlew.bat assembleDebug`. This will download build dependencies when you
   explicitly run it. Keep local.properties and keystores out of Git.
5. APK: app/build/outputs/apk/debug/app-debug.apk.
   `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
6. Run backend on Windows, then `adb reverse tcp:8000 tcp:8000`.
   Login backend address: http://127.0.0.1:8000; emulator alternative:
   http://10.0.2.2:8000. Enter the administrator-issued token manually.
7. Grant notes/reminders and optional stub tools locally; run scheduler in a separate
   process. Test owner isolation and confirmation expiry/replay before real use.

Release denies cleartext HTTP. Use a trusted HTTPS deployment for remote access.
Never ship a certificate-validation bypass. Network requests disable redirects.
Credentials stay in RAM, are excluded from saved view state/autofill, and are cleared
on logout/process death. Android backup is disabled and sensitive screens block
screenshots. Async results from previous screens/accounts are ignored.
No automatic request retries or background action queue.

## Still required before installation/release

Build/lint/instrumentation on a real SDK, device tests, release signing, accessibility,
rotation/state restoration, secure optional credential persistence using Keystore,
voice permission/implementation and OS notifications. The current reminder inbox is
not a background push notification service. Do not distribute as a production APK
without these checks.
