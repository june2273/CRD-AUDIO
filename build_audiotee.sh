#!/bin/sh
# audiotee(Core Audio 탭 캡처 CLI)를 고정 커밋으로 받아 빌드한다. API가 불안정하므로 커밋을 올릴 땐 직접 확인 후 변경.
# 필요: Xcode Command Line Tools (swift). Xcode 앱은 불필요.
set -e
COMMIT=56ac954369
cd "$(dirname "$0")"
[ -d vendor/audiotee ] || git clone https://github.com/makeusabrew/audiotee.git vendor/audiotee
git -C vendor/audiotee fetch -q origin
git -C vendor/audiotee checkout -q "$COMMIT"
(cd vendor/audiotee && swift build -c release)
echo "OK: vendor/audiotee/.build/release/audiotee"
