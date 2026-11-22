#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${ANDROID_SDK_ROOT:-}" ]]; then
    SDK_ROOT="$ANDROID_SDK_ROOT"
elif [[ -n "${ANDROID_HOME:-}" ]]; then
    SDK_ROOT="$ANDROID_HOME"
elif [[ -d "${HOME}/Android/Sdk" ]]; then
    SDK_ROOT="${HOME}/Android/Sdk"
else
    SDK_ROOT="/usr/lib/android-sdk"
fi
JAVA_CMD=java
if [[ -n "${JAVA_HOME:-}" ]]; then
    JAVA_CMD="$JAVA_HOME/bin/java"
fi

if ! command -v "$JAVA_CMD" >/dev/null 2>&1; then
    echo "Java 17 ou plus récent est requis. Installe Android Studio ou un JDK 17+." >&2
    exit 1
fi

JAVA_VERSION="$("$JAVA_CMD" -version 2>&1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' | head -n 1)"
if [[ -z "$JAVA_VERSION" || "$JAVA_VERSION" -lt 17 ]]; then
    echo "Java 17 ou plus récent est requis; version détectée : ${JAVA_VERSION:-inconnue}." >&2
    exit 1
fi

if [[ ! -f "$SDK_ROOT/platforms/android-37/android.jar" && ! -f "$SDK_ROOT/platforms/android-37.0/android.jar" ]] || [[ ! -d "$SDK_ROOT/build-tools/36.0.0" ]]; then
    cat >&2 <<EOF
Android SDK Platform 37 et Build Tools 36.0.0 sont requis.
SDK détecté : $SDK_ROOT
Dans Android Studio, ouvre Settings → Android SDK et installe :
  - Android SDK Platform 37
  - Android SDK Build-Tools 36.0.0
Puis relance ce script. Pour un autre emplacement, définis ANDROID_SDK_ROOT.
EOF
    exit 1
fi

export ANDROID_SDK_ROOT="$SDK_ROOT"
"$PROJECT_DIR/gradlew" -p "$PROJECT_DIR" assembleDebug

APK_PATH="$PROJECT_DIR/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "$APK_PATH" ]]; then
    echo "Gradle a terminé sans produire l’APK attendu : $APK_PATH" >&2
    exit 1
fi

echo "APK prêt : $APK_PATH"
