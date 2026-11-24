# Phone app

A small Android app: a WebView running the same control page as the board (`assets/index.html`, copied from
`firmware/ledctl/page.html`), plus a Bluetooth LE bridge so it works without Wi-Fi. It can also stream the
phone's own audio levels to the board.

Built without Gradle: see `build.sh` (aapt, javac, dalvik-exchange, zipalign, apksigner). It creates a debug
signing key on first build; the key is not in the repo.
