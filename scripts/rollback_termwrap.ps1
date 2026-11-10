$ErrorActionPreference = 'Stop'
# 从 fix_termwrap.ps1 写出的备份文件恢复原始 ServiceDll
$log = 'C:\Users\Public\rollback_termwrap_result.txt'
$out = New-Object System.Collections.ArrayList
function Say($t) { [void]$out.Add($t); Write-Host $t }

try {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters'
    $backupFile = 'C:\Users\Public\termwrap_backup.json'

    if (-not (Test-Path $backupFile)) {
        throw "backup file not found: $backupFile (run fix_termwrap.bat first)"
    }
    $backup = Get-Content $backupFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $orig = $backup.ServiceDll
    Say ("restoring ServiceDll -> " + $orig)

    Set-ItemProperty $key -Name ServiceDll -Value $orig -Type ExpandString
    $now = (Get-ItemProperty $key -Name ServiceDll).ServiceDll
    Say ("after : ServiceDll = " + $now)

    $ok = ($now -eq $orig)
    Say ''
    Say ('RESULT: ' + $(if ($ok) { 'OK - reboot required to take effect' } else { 'FAILED - value not written' }))
}
catch {
    Say ('EXCEPTION: ' + $_.Exception.Message)
}

$out | Set-Content -Path $log -Encoding UTF8
Write-Host ''
Write-Host ('Log: ' + $log)
