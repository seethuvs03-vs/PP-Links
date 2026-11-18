param(
    [Parameter(Mandatory = $true)]
    [System.Net.IPAddress]$LocalAddress
)

$ErrorActionPreference = 'Stop'
$ruleName = 'PPLinks-Office-LAN-5000'
$resultPath = Join-Path $PSScriptRoot 'outputs/firewall-setup-result.txt'
try {
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -Name $ruleName -DisplayName 'PP Links - Office LAN TCP 5000' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 -LocalAddress $LocalAddress -RemoteAddress LocalSubnet -InterfaceAlias 'Wi-Fi' -Profile Any | Out-Null
    } else {
        Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias 'Wi-Fi'
        Get-NetFirewallRule -Name $ruleName | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort 5000
        Get-NetFirewallRule -Name $ruleName | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -LocalAddress $LocalAddress -RemoteAddress LocalSubnet
        Get-NetFirewallRule -Name $ruleName | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program Any
    }
    ('SUCCESS '+(Get-Date -Format o)+" - TCP 5000 allowed on Wi-Fi $LocalAddress from LocalSubnet, all network profiles.") | Set-Content -LiteralPath $resultPath
} catch {
    $_.Exception.Message | Set-Content -LiteralPath $resultPath
    exit 1
}
