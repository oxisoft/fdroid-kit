# Play automatic protection (com.pairip)

Play Console can add "automatic protection" to an app. Play then injects a licence check
(classes under `com.pairip`) into the APKs it generates. On a device where the app was not
installed by Play, the check shows a "get this app from Play" wall and the app is unusable.

Why it bites F-Droid repos:

- The universal APK you download from Play for F-Droid carries the check too.
- It may not trigger on the first launch, so "it opened on my test phone" proves nothing.

What fdroidkit does: `publish` scans every `classes*.dex` in the APK for `Lcom/pairip/` and refuses
to publish when it finds it. The fix is in Play Console: turn automatic protection off for the app,
then build a new release.
