#include <windows.h>
#include <stdio.h>
#include <string.h>

extern FILE* g_log;
extern void patch(HMODULE hMod);

int wmain()
{
    g_log = fopen("C:\\Users\\Public\\termwrap_patchtest.log", "w");
    if (g_log) { fprintf(g_log, "=== load termsrv.dll ===\n"); fflush(g_log); }

    HMODULE h = LoadLibraryExW(L"termsrv.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!h)
    {
        printf("LoadLibraryEx failed, err=%lu\n", GetLastError());
        if (g_log) { fprintf(g_log, "LoadLibraryEx FAILED err=%lu\n", GetLastError()); fclose(g_log); }
        return 1;
    }
    printf("termsrv.dll base = %p\n", h);
    if (g_log) { fprintf(g_log, "base = %p\n", h); fflush(g_log); }

    /* snapshot .text so we can prove patch() really wrote bytes */
    BYTE* base = (BYTE*)h;
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    DWORD textOff = 0, textSize = 0;
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; i++)
    {
        if (!memcmp(sec[i].Name, ".text", 5)) { textOff = sec[i].VirtualAddress; textSize = sec[i].Misc.VirtualSize; break; }
    }
    BYTE* before = NULL;
    if (textSize)
    {
        before = (BYTE*)malloc(textSize);
        memcpy(before, base + textOff, textSize);
    }

    patch(h);

    int changed = 0;
    if (before)
    {
        for (DWORD i = 0; i < textSize; i++) if (before[i] != base[textOff + i]) changed++;
        if (g_log) { fprintf(g_log, "=== .text size=%lu changedBytes=%d ===\n", textSize, changed); }
        printf(".text size=%lu changedBytes=%d\n", textSize, changed);
        free(before);
    }

    printf("patch() returned normally\n");
    if (g_log) { fprintf(g_log, "=== patch() returned normally ===\n"); fclose(g_log); }
    return 0;
}
