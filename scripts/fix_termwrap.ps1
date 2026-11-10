$ErrorActionPreference = 'Stop'
$log = 'C:\Users\Public\fix_termwrap_result.txt'
$out = New-Object System.Collections.ArrayList
function Say($t) { [void]$out.Add($t); Write-Host $t }

try {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters'
    $tw  = 'C:\Program Files\RDP Wrapper\TermWrap.dll'

    # 仓库内自带预编译 TermWrap.dll（3rd/），目标位置缺失时先复制过去
    if (-not (Test-Path $tw)) {
        $repoDll = Join-Path (Split-Path $PSScriptRoot -Parent) '3rd\TermWrap.dll'
        if (Test-Path $repoDll) {
            New-Item -ItemType Directory -Force -Path (Split-Path $tw) | Out-Null
            Copy-Item $repoDll $tw -Force
            Say "copied TermWrap.dll from repo: $repoDll -> $tw"
        }
    }

    if (-not (Test-Path $tw)) { Say "ERROR: TermWrap.dll not found at $tw"; throw 'missing dll' }

    $cur = (Get-ItemProperty $key -Name ServiceDll -ErrorAction SilentlyContinue).ServiceDll
    Say ("before: ServiceDll = " + $cur)

    # backup for rollback
    @{ ServiceDll = $cur; Time = (Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -Path 'C:\Users\Public\termwrap_backup.json' -Encoding UTF8
    Say 'backup written: C:\Users\Public\termwrap_backup.json'

    Set-ItemProperty $key -Name ServiceDll -Value $tw -Type ExpandString
    $now = (Get-ItemProperty $key -Name ServiceDll).ServiceDll
    Say ("after : ServiceDll = " + $now)

    # classic concurrent session flag
    $lic = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\Licensing Core'
    if (-not (Test-Path $lic)) { New-Item -Path $lic -Force | Out-Null; Say 'created Licensing Core key' }
    $before = (Get-ItemProperty $lic -Name EnableConcurrentSessions -ErrorAction SilentlyContinue).EnableConcurrentSessions
    Set-ItemProperty $lic -Name EnableConcurrentSessions -Value 1 -Type DWord
    Say ("EnableConcurrentSessions: " + $before + " -> 1")

    $ok = ($now -eq $tw)
    Say ''
    Say ('RESULT: ' + $(if ($ok) { 'OK - reboot required to take effect' } else { 'FAILED - value not written' }))
}
catch {
    Say ('EXCEPTION: ' + $_.Exception.Message)
}

$out | Set-Content -Path $log -Encoding UTF8
Write-Host ''
Write-Host ('Log: ' + $log)
