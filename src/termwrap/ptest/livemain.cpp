#include <windows.h>
#include <winsvc.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

extern FILE* g_log;
extern void patch(HMODULE hMod);
extern size_t g_dw1, g_dw2;

static void lg(const char* fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    printf("%s\n", buf);
    if (g_log) { fprintf(g_log, "%s\n", buf); fflush(g_log); }
}

static BOOL EnableDebugPrivilege(void)
{
    HANDLE tok;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &tok)) return FALSE;
    TOKEN_PRIVILEGES tp;
    tp.PrivilegeCount = 1;
    if (!LookupPrivilegeValueA(NULL, SE_DEBUG_NAME, &tp.Privileges[0].Luid)) { CloseHandle(tok); return FALSE; }
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    AdjustTokenPrivileges(tok, FALSE, &tp, sizeof(tp), NULL, NULL);
    BOOL ok = (GetLastError() == ERROR_SUCCESS);
    CloseHandle(tok);
    return ok;
}

static BOOL IsElevated(void)
{
    HANDLE tok;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &tok)) return FALSE;
    TOKEN_ELEVATION e; DWORD ret = 0;
    BOOL ok = GetTokenInformation(tok, TokenElevation, &e, sizeof(e), &ret) && e.TokenIsElevated;
    CloseHandle(tok);
    return ok;
}

static DWORD FindTermServicePid(void)
{
    SC_HANDLE scm = OpenSCManagerA(NULL, NULL, SC_MANAGER_CONNECT);
    if (!scm) { lg("OpenSCManager failed err=%lu", GetLastError()); return 0; }
    SC_HANDLE svc = OpenServiceA(scm, "TermService", SERVICE_QUERY_STATUS);
    if (!svc) { lg("OpenService(TermService) failed err=%lu", GetLastError()); CloseServiceHandle(scm); return 0; }
    SERVICE_STATUS_PROCESS ssp; DWORD need = 0;
    DWORD pid = 0;
    if (QueryServiceStatusEx(svc, SC_STATUS_PROCESS_INFO, (BYTE*)&ssp, sizeof(ssp), &need))
        pid = ssp.dwProcessId;
    else
        lg("QueryServiceStatusEx failed err=%lu", GetLastError());
    CloseServiceHandle(svc); CloseServiceHandle(scm);
    return pid;
}

static size_t FindModuleInProcess(DWORD pid, const char* want)
{
    size_t found = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
    if (snap == INVALID_HANDLE_VALUE) { lg("snapshot failed err=%lu", GetLastError()); return 0; }
    MODULEENTRY32W me; me.dwSize = sizeof(me);
    if (Module32FirstW(snap, &me)) {
        do {
            char name[MAX_PATH];
            WideCharToMultiByte(CP_ACP, 0, me.szModule, -1, name, sizeof(name), NULL, NULL);
            if (!_stricmp(name, want)) { found = (size_t)me.modBaseAddr; break; }
        } while (Module32NextW(snap, &me));
    } else lg("Module32First failed err=%lu", GetLastError());
    CloseHandle(snap);
    return found;
}

struct Run { size_t off; unsigned int len; unsigned char bytes[32]; };

static int CollectRuns(const BYTE* before, const BYTE* after, DWORD size, Run* runs, int maxRuns)
{
    int n = 0;
    DWORD i = 0;
    while (i < size && n < maxRuns) {
        if (before[i] != after[i]) {
            DWORD start = i;
            while (i < size && before[i] != after[i]) i++;
            DWORD len = i - start;
            if (len > 32) len = 32;
            runs[n].off = start; runs[n].len = len;
            memcpy(runs[n].bytes, after + start, len);
            n++;
        } else i++;
    }
    return n;
}

static BOOL WriteRemote(HANDLE hp, void* addr, const void* buf, size_t len)
{
    DWORD old = 0;
    if (!VirtualProtectEx(hp, addr, len, PAGE_EXECUTE_READWRITE, &old)) {
        lg("VirtualProtectEx failed @%p err=%lu", addr, GetLastError());
        return FALSE;
    }
    SIZE_T written = 0;
    BOOL ok = WriteProcessMemory(hp, addr, buf, len, &written);
    DWORD old2 = 0;
    VirtualProtectEx(hp, addr, len, old, &old2);
    if (!ok) lg("WriteProcessMemory failed @%p err=%lu", addr, GetLastError());
    return ok && written == len;
}

static BOOL ReadRemoteDword(HANDLE hp, size_t addr, DWORD* val)
{
    SIZE_T rd = 0;
    return ReadProcessMemory(hp, (void*)addr, val, 4, &rd) && rd == 4;
}

static void SetRegistryPersistence(void)
{
    // ServiceDll -> TermWrap.dll (takes effect at next natural reboot)
    const char* tw = "C:\\Program Files\\RDP Wrapper\\TermWrap.dll";
    if (GetFileAttributesA(tw) == INVALID_FILE_ATTRIBUTES) {
        lg("SKIP registry: TermWrap.dll not found at %s", tw);
        return;
    }
    HKEY k;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Services\\TermService\\Parameters", 0, KEY_READ | KEY_SET_VALUE, &k)) {
        lg("RegOpen Parameters failed err=%lu", GetLastError()); return;
    }
    char cur[512]; DWORD curType = 0, curLen = sizeof(cur);
    BOOL had = RegQueryValueExA(k, "ServiceDll", NULL, &curType, (BYTE*)cur, &curLen) == ERROR_SUCCESS;
    if (had) lg("ServiceDll before: %s", cur);
    // backup once
    if (had && GetFileAttributesA("C:\\Users\\Public\\termwrap_backup.json") == INVALID_FILE_ATTRIBUTES) {
        FILE* f = fopen("C:\\Users\\Public\\termwrap_backup.json", "w");
        if (f) { fprintf(f, "{\"ServiceDll\": \"%s\", \"Time\": \"live-patch\"}", cur); fclose(f); lg("backup written"); }
    }
    if (!had || strcmp(cur, tw)) {
        RegSetValueExA(k, "ServiceDll", 0, REG_EXPAND_SZ, (const BYTE*)tw, (DWORD)strlen(tw) + 1);
        char now[512]; DWORD n2 = sizeof(now);
        RegQueryValueExA(k, "ServiceDll", NULL, NULL, (BYTE*)now, &n2);
        lg("ServiceDll after : %s", now);
    } else lg("ServiceDll already points to TermWrap");
    RegCloseKey(k);

    // EnableConcurrentSessions = 1
    HKEY lic;
    if (!RegCreateKeyExA(HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\Licensing Core", 0, NULL, 0, KEY_SET_VALUE, NULL, &lic, NULL)) {
        DWORD one = 1;
        RegSetValueExA(lic, "EnableConcurrentSessions", 0, REG_DWORD, (const BYTE*)&one, 4);
        RegCloseKey(lic);
        lg("EnableConcurrentSessions = 1");
    }
}

int main(void)
{
    g_log = fopen("C:\\Users\\Public\\termwrap_live.log", "w");
    lg("=== TermWrap live patcher ===");

    if (!IsElevated()) { lg("ERROR: not elevated, run as admin"); if (g_log) fclose(g_log); return 1; }
    lg("elevated: yes");
    lg("SeDebugPrivilege: %s", EnableDebugPrivilege() ? "enabled" : "FAILED");

    DWORD pid = FindTermServicePid();
    if (!pid) { lg("ERROR: cannot find TermService PID"); if (g_log) fclose(g_log); return 1; }
    lg("TermService PID = %lu", pid);

    HANDLE hp = OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!hp) { lg("ERROR: OpenProcess failed err=%lu", GetLastError()); if (g_log) fclose(g_log); return 1; }

    size_t targetBase = FindModuleInProcess(pid, "termsrv.dll");
    if (!targetBase) { lg("ERROR: termsrv.dll not found in target process"); CloseHandle(hp); if (g_log) fclose(g_log); return 1; }
    lg("target termsrv.dll base = 0x%zx", targetBase);

    // load a local copy (same boot -> same ASLR base for system DLLs)
    HMODULE h = LoadLibraryExW(L"termsrv.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!h) { lg("ERROR: LoadLibraryEx failed err=%lu", GetLastError()); CloseHandle(hp); if (g_log) fclose(g_log); return 1; }
    lg("local  termsrv.dll base = 0x%zx", (size_t)h);
    if ((size_t)h != targetBase) {
        lg("ERROR: base mismatch (target 0x%zx vs local 0x%zx) - abort", targetBase, (size_t)h);
        CloseHandle(hp); if (g_log) fclose(g_log); return 1;
    }

    BYTE* base = (BYTE*)h;
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    DWORD textOff = 0, textSize = 0;
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; i++)
        if (!memcmp(sec[i].Name, ".text", 5)) { textOff = sec[i].VirtualAddress; textSize = sec[i].Misc.VirtualSize; break; }
    if (!textSize) { lg("ERROR: .text not found"); CloseHandle(hp); if (g_log) fclose(g_log); return 1; }

    BYTE* before = (BYTE*)malloc(textSize);
    memcpy(before, base + textOff, textSize);

    patch(h);

    int changedBytes = 0;
    for (DWORD i = 0; i < textSize; i++) if (before[i] != base[textOff + i]) changedBytes++;
    lg("local patch changedBytes = %d (expected 33)", changedBytes);

    Run runs[64];
    int nRuns = CollectRuns(before, base + textOff, textSize, runs, 64);
    lg("changed runs = %d", nRuns);
    for (int i = 0; i < nRuns; i++)
        lg("  run[%d] off=0x%zx len=%u", i, runs[i].off, runs[i].len);

    // check whether target is already patched: compare first run bytes
    int already = 1;
    for (int i = 0; i < nRuns; i++) {
        BYTE cur[32]; SIZE_T rd = 0;
        if (!ReadProcessMemory(hp, base + textOff + runs[i].off, cur, runs[i].len, &rd) || rd != runs[i].len ||
            memcmp(cur, runs[i].bytes, runs[i].len)) { already = 0; break; }
    }
    lg("target already patched: %s", already ? "YES (skip .text write)" : "no");

    if (!already) {
        int okAll = 1;
        for (int i = 0; i < nRuns; i++) {
            void* addr = base + textOff + runs[i].off;
            if (!WriteRemote(hp, addr, runs[i].bytes, runs[i].len)) { okAll = 0; break; }
            else lg("  wrote run[%d] @%p", i, addr);
        }
        if (!okAll) { lg("ERROR: .text remote write failed"); free(before); CloseHandle(hp); if (g_log) fclose(g_log); return 1; }
        // verify readback
        int bad = 0;
        for (int i = 0; i < nRuns; i++) {
            BYTE cur[32]; SIZE_T rd = 0;
            ReadProcessMemory(hp, base + textOff + runs[i].off, cur, runs[i].len, &rd);
            if (rd != runs[i].len || memcmp(cur, runs[i].bytes, runs[i].len)) { bad++; lg("verify mismatch run[%d]", i); }
        }
        lg(".text verify: %s", bad ? "FAILED" : "OK");
    }

    // .data writes: AllowRemote flag inside CSLQuery_Initialize + bInitialized
    size_t dwAddrs[2] = { g_dw1, g_dw2 };
    const char* dwNames[2] = { "CSLQuery data write", "bInitialized" };
    for (int i = 0; i < 2; i++) {
        if (!dwAddrs[i]) { lg("%s: not recorded, skip", dwNames[i]); continue; }
        DWORD cur = 0;
        if (!ReadRemoteDword(hp, dwAddrs[i], &cur)) { lg("%s: read failed @0x%zx err=%lu", dwNames[i], dwAddrs[i], GetLastError()); continue; }
        lg("%s @0x%zx current=0x%lx", dwNames[i], dwAddrs[i], cur);
        DWORD one = 1;
        if (WriteRemote(hp, (void*)dwAddrs[i], &one, 4)) {
            DWORD now = 0; ReadRemoteDword(hp, dwAddrs[i], &now);
            lg("%s -> 1 (readback=0x%lx)", dwNames[i], now);
        } else lg("%s: write FAILED", dwNames[i]);
    }

    free(before);
    CloseHandle(hp);

    SetRegistryPersistence();

    lg("RESULT: OK - termsrv patched in live TermService process, no reboot needed");
    if (g_log) fclose(g_log);
    printf("Done. See C:\\Users\\Public\\termwrap_live.log\n");
    return 0;
}
