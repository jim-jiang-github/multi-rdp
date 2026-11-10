$ErrorActionPreference = 'Continue'
# users.json 在仓库内 src/webui 下（相对本脚本定位，不再硬编码绝对路径）
$json = Join-Path $PSScriptRoot '..\src\webui\users.json'
$log  = 'C:\Users\Public\grant_admin_result.txt'
$out = New-Object System.Collections.Generic.List[string]

$pr = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath) -Verb RunAs
    exit
}

$names = New-Object System.Collections.Generic.List[string]
if (Test-Path $json) {
    try {
        $db = Get-Content $json -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $db.PSObject.Properties) { $names.Add($p.Name) }
    } catch { $out.Add("WARN  cannot read users.json: $($_.Exception.Message)") }
}
try {
    Get-LocalGroupMember -Group 'Remote Desktop Users' -ErrorAction Stop |
        ForEach-Object { $names.Add(($_.Name -split '\\')[-1]) }
} catch { $out.Add("WARN  cannot read Remote Desktop Users: $($_.Exception.Message)") }

$admins = @()
try {
    $admins = Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop |
        ForEach-Object { ($_.Name -split '\\')[-1].ToLower() }
} catch {}

$names = $names | Sort-Object -Unique
if (-not $names) { $out.Add('No target user found.') }

foreach ($n in $names) {
    if ([string]::IsNullOrWhiteSpace($n)) { continue }
    if ($n -match '^(administrator|guest|defaultaccount|wdagutilityaccount)$') { continue }
    if ($admins -contains $n.ToLower()) {
        $out.Add("SKIP  $n  (already administrator)")
        continue
    }
    try {
        Add-LocalGroupMember -Group 'Administrators' -Member $n -ErrorAction Stop
        $out.Add("OK    $n  -> Administrators")
    } catch {
        $out.Add("FAIL  $n  : $($_.Exception.Message)")
    }
}

$out.Add('')
$out.Add('Done. Sign the remote session out and back in for it to take effect.')
$out | Set-Content -Path $log -Encoding UTF8
$out | ForEach-Object { Write-Host $_ }
