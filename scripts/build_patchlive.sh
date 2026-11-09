#!/usr/bin/env bash
# Build the TermWrap LIVE patcher: patches the running TermService svchost process memory
# (same 33-byte patch TermWrap would apply at service start), no reboot / no service restart.
set -u
export MSYS2_ARG_CONV_EXCL='*'
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # repo root
OUT="$ROOT/build_out"
TW="$ROOT/src/termwrap"
PT="$ROOT/src/termwrap/ptest"
ZYDIS="$ROOT/3rd/zydis"
ZYCORE="$ZYDIS/dependencies/zycore"
MSVC="C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.40.33807"
CL="$MSVC/bin/Hostx64/x64/cl.exe"
LINK="$MSVC/bin/Hostx64/x64/link.exe"
SDK="C:/Program Files (x86)/Windows Kits/10"
INC10="$SDK/Include/10.0.26100.0"

DEFS="-DNDEBUG -D_CRT_SECURE_NO_WARNINGS -DZYDIS_STATIC_BUILD -DZYCORE_STATIC_BUILD -DWIN32 -D_WINDOWS"
INC="-I$ZYCORE/include -I$ZYDIS/include -I$TW"

"$CL" -nologo -c -O1 -MT $DEFS $INC \
  -I"$MSVC/include" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
  -Fo"$PT/PatchLive.obj" "$PT/PatchLive.cpp" || exit 1

"$CL" -nologo -c -O1 -MT $DEFS \
  -I"$MSVC/include" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
  -Fo"$PT/livemain.obj" "$PT/livemain.cpp" || exit 1

"$LINK" -nologo -OUT:"$PT/patchlive.exe" -MACHINE:X64 -SUBSYSTEM:CONSOLE -INCREMENTAL:NO \
  -LIBPATH:"$SDK/Lib/10.0.26100.0/um/x64" -LIBPATH:"$SDK/Lib/10.0.26100.0/ucrt/x64" -LIBPATH:"$MSVC/lib/x64" \
  "$PT/livemain.obj" "$PT/PatchLive.obj" "$OUT/Zydis.lib" "$OUT/Zycore.lib" \
  kernel32.lib advapi32.lib user32.lib || exit 1

echo BUILD_OK
ls -la "$PT/patchlive.exe"
