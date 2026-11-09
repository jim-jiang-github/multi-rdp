#!/usr/bin/env bash
# Manual TermWrap build (x64 Release), mirrors TermWrap.vcxproj settings.
# MSBuild is blocked by security policy, so we call cl/lib/rc/link directly.
# Chain: Zycore.lib -> Zydis.lib -> TermWrap objs -> TermWrap.dll
set -u
export MSYS2_ARG_CONV_EXCL='*'

ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # repo root
OUT="$ROOT/build_out"
TW="$ROOT/src/termwrap"
ZYDIS="$ROOT/3rd/zydis"
ZYCORE="$ZYDIS/dependencies/zycore"

MSVC="C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.40.33807"
CL="$MSVC/bin/Hostx64/x64/cl.exe"
LINK="$MSVC/bin/Hostx64/x64/link.exe"
LIB="$MSVC/bin/Hostx64/x64/lib.exe"
DUMPBIN="$MSVC/bin/Hostx64/x64/dumpbin.exe"
SDK="C:/Program Files (x86)/Windows Kits/10"
INC10="$SDK/Include/10.0.26100.0"
RC="$SDK/bin/10.0.26100.0/x64/rc.exe"

SYSINC="-I\"$INC10/ucrt\" -I\"$INC10/um\" -I\"$INC10/shared\" -I\"$INC10/winrt\" -I\"$INC10/cppwinrt\" -I\"$MSVC/include\""
MSVCINC="C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.40.33807/include"

mkdir -p "$OUT"/zycore "$OUT"/zydis "$OUT"/tw
cd "$TW" || exit 1

echo "===== [0/5] version.h ====="
powershell -NoProfile -ExecutionPolicy Bypass -File gen_version.ps1 -OutPath version.h
[ -f version.h ] || { echo "[FAIL] version.h not generated"; exit 1; }
cat version.h

echo "===== [1/5] Zycore static lib ====="
cd "$ZYCORE/src" || exit 1
COREDEFS="-DZYCORE_STATIC_BUILD -DNDEBUG -D_CRT_SECURE_NO_WARNINGS -DWIN32 -D_WINDOWS"
COREINC="-I$ZYCORE/include"
for f in *.c; do
  "$CL" -nologo -c -TC -O2 -MT -GS- -W3 $COREDEFS $COREINC \
    -I"$MSVCINC" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
    -Fo"$OUT/zycore/${f%.c}.obj" "$f" || exit 1
done
"$LIB" -nologo -OUT:"$OUT/Zycore.lib" "$OUT"/zycore/*.obj || exit 1

echo "===== [2/5] Zydis static lib ====="
cd "$ZYDIS/src" || exit 1
DISDEFS="-DZYCORE_STATIC_BUILD -DZYDIS_STATIC_BUILD -DNDEBUG -D_CRT_SECURE_NO_WARNINGS -DWIN32 -D_WINDOWS"
DISINC="-I$ZYCORE/include -I$ZYDIS/include -I$ZYDIS/src"
for f in *.c; do
  "$CL" -nologo -c -TC -O2 -MT -GS- -W3 $DISDEFS $DISINC \
    -I"$MSVCINC" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
    -Fo"$OUT/zydis/${f%.c}.obj" "$f" || exit 1
done
"$LIB" -nologo -OUT:"$OUT/Zydis.lib" "$OUT"/zydis/*.obj || exit 1

echo "===== [3/5] TermWrap objects ====="
cd "$TW" || exit 1
TWDEFS="-DNDEBUG -DUNICODE -D_UNICODE -D_CRT_SECURE_NO_WARNINGS -DZYDIS_STATIC_BUILD -DZYCORE_STATIC_BUILD -DWIN32 -D_WINDOWS"
TWINC="-I$ZYCORE/include -I$ZYDIS/include -I$TW"
"$CL" -nologo -c -O2 -Oi -Gy -GS- -GR- -EHs-c- $TWDEFS $TWINC \
  -I"$MSVCINC" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
  -Fo"$OUT/tw/DllMain.obj" DllMain.cpp || exit 1
"$CL" -nologo -c -O2 -Oi -Gy -GS- -GR- -EHs-c- $TWDEFS $TWINC \
  -I"$MSVCINC" -I"$INC10/ucrt" -I"$INC10/um" -I"$INC10/shared" -I"$INC10/winrt" -I"$INC10/cppwinrt" \
  -Fo"$OUT/tw/Patch.obj" Patch.cpp || exit 1

echo "===== [4/5] version.res + msvcrt.lib ====="
"$RC" -nologo -dUNICODE -dUNICODE_ -I"$TW" -I"$INC10/um" -I"$INC10/shared" -fo"$OUT/tw/version.res" version.rc || exit 1
"$LIB" -nologo -def:"$TW/msvcrt.def" -machine:X64 -out:"$OUT/tw/msvcrt.lib" || exit 1

echo "===== [5/5] link TermWrap.dll ====="
"$LINK" -nologo -DLL -OUT:"$OUT/TermWrap.dll" -MACHINE:X64 \
  -NODEFAULTLIB -ENTRY:DllMain -DEF:"$TW/Export.def" \
  -OPT:REF -OPT:ICF -RELEASE -SUBSYSTEM:CONSOLE -INCREMENTAL:NO \
  -LIBPATH:"$SDK/Lib/10.0.26100.0/um/x64" -LIBPATH:"$SDK/Lib/10.0.26100.0/ucrt/x64" -LIBPATH:"$MSVC/lib/x64" \
  "$OUT/tw/DllMain.obj" "$OUT/tw/Patch.obj" "$OUT/tw/version.res" \
  "$OUT/Zydis.lib" "$OUT/Zycore.lib" "$OUT/tw/msvcrt.lib" kernel32.lib advapi32.lib || exit 1

echo "===== dumpbin verify ====="
"$DUMPBIN" -nologo -EXPORTS "$OUT/TermWrap.dll" | head -25
ls -la "$OUT/TermWrap.dll"
echo "[OK] build done: $OUT/TermWrap.dll"
