$log = 'C:\Users\Public\termwrap_status.txt'
$r = New-Object System.Collections.ArrayList
function Say($t) { [void]$r.Add($t) }

$key = 'HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters'
Say ("ServiceDll = " + (Get-ItemProperty $key -Name ServiceDll -ErrorAction SilentlyContinue).ServiceDll)

$pid2 = (Get-CimInstance Win32_Service -Filter "Name='TermService'").ProcessId
Say ("TermService pid = " + $pid2)
$p = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
if ($p) {
    $mods = $p.Modules | Where-Object { $_.ModuleName -match 'TermWrap|Zydis|termsrv' } | Select-Object -ExpandProperty ModuleName
    if ($mods) { Say ("loaded modules: " + ($mods -join ', ')) } else { Say 'loaded modules: NONE (TermWrap not loaded)' }
} else { Say 'TermService process not found' }

$lic = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\Licensing Core'
Say ("EnableConcurrentSessions = " + (Get-ItemProperty $lic -Name EnableConcurrentSessions -ErrorAction SilentlyContinue).EnableConcurrentSessions)

Say ''
Say 'Sessions:'
foreach ($ln in (query session 2>$null)) { Say ('  ' + $ln) }

$r | Set-Content -Path $log -Encoding UTF8
Write-Host ('written: ' + $log)
