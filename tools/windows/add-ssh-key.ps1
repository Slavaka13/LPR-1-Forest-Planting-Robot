# Copies this PC's SSH public key to the Raspberry Pi (one-time setup).
# Enter the Raspberry Pi password when ssh asks for it.
$pub = Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"
Write-Host "Enter the Raspberry Pi password when asked (characters are not shown):" -ForegroundColor Yellow
$pub | ssh -o StrictHostKeyChecking=accept-new biba@192.168.31.81 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo KEY-OK"
