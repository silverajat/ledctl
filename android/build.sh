#!/bin/sh
# Builds ledctl.apk without Gradle (Ubuntu: apt install android-sdk-platform-23 aapt dalvik-exchange apksigner zipalign openjdk-17-jdk-headless)
set -e
cd "$(dirname "$0")"
J=/usr/lib/android-sdk/platforms/android-23/android.jar
cp ../firmware/ledctl/page.html assets/index.html
rm -rf build && mkdir -p build/gen build/obj
aapt package -f -m -J build/gen -M AndroidManifest.xml -S res -A assets -I $J -F build/app.tmp.apk
javac -nowarn -source 8 -target 8 -bootclasspath $J -classpath $J -d build/obj src/sg/rajat/ledctl/MainActivity.java build/gen/sg/rajat/ledctl/R.java
dalvik-exchange --dex --min-sdk-version=23 --output=build/classes.dex build/obj
(cd build && aapt add app.tmp.apk classes.dex >/dev/null && zipalign -f 4 app.tmp.apk app.aligned.apk)
[ -f debug.keystore ] || keytool -genkeypair -keystore debug.keystore -storepass ledctl123 -keypass ledctl123 -alias ledctl -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=ledctl"
apksigner sign --ks debug.keystore --ks-pass pass:ledctl123 --key-pass pass:ledctl123 --out build/ledctl.apk build/app.aligned.apk
echo built build/ledctl.apk
