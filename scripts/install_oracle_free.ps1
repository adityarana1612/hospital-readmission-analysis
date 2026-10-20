<#
.SYNOPSIS
    Silent install of Oracle AI Database 26ai Free on Windows, then creates the
    project schema user and stores its credentials securely.

.DESCRIPTION
    Must be run from an ELEVATED PowerShell (Run as Administrator).

    Security design:
      * Passwords are read with Read-Host -AsSecureString (never echoed, never
        passed on a command line, never written to the project folder).
      * Oracle's installer needs a response file containing the admin password.
        It is written to %TEMP%, used once, and deleted in a finally{} block.
      * The application password is stored in Windows Credential Manager via the
        Python 'keyring' package (encrypted per Windows user), not in a file
        or environment variable.
      * Only NON-secret settings (ORACLE_USER, ORACLE_DSN) are written as
        user-level environment variables.

.PARAMETER InstallerDir   Folder containing setup.exe (extracted Oracle zip).
.PARAMETER InstallDir     Oracle home location.
.PARAMETER ProjectDir     Project root (used to find the venv Python for keyring).
#>
param(
    [string]$InstallerDir = "D:\oracle-installer\setup",
    [string]$InstallDir   = "D:\app\oracle\product\26ai\",
    [string]$ProjectDir   = "D:\hospital-readmission-analysis",
    [string]$AppUser      = "HRRA",
    [int]$ListenerPort    = 1521
)
$ErrorActionPreference = "Stop"

function Test-Admin {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-ConfirmedPassword([string]$Label) {
    while ($true) {
        $a = Read-Host "Enter $Label password" -AsSecureString
        $b = Read-Host "Confirm $Label password" -AsSecureString
        $pa = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($a))
        $pb = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($b))
        if ($pa -ne $pb) { Write-Warning "Passwords do not match."; continue }
        if ($pa.Length -lt 8) { Write-Warning "Use at least 8 characters."; continue }
        if ($pa -match '["@/\\\s]') { Write-Warning 'Avoid " @ / \ and spaces (they break Oracle connect strings).'; continue }
        return $pa
    }
}

if (-not (Test-Admin)) { throw "Run this script from an elevated PowerShell (Run as Administrator)." }
$setup = Join-Path $InstallerDir "setup.exe"
if (-not (Test-Path $setup)) { throw "setup.exe not found in $InstallerDir" }

# ---------------------------------------------------------------- 1. Install
$existing = Get-Service -Name "OracleServiceFREE" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "OracleServiceFREE already exists - skipping software install." -ForegroundColor Yellow
    $sysPwd = Read-Host "Enter the EXISTING SYS/SYSTEM password" -AsSecureString
    $sysPwd = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sysPwd))
} else {
    $sysPwd = Read-ConfirmedPassword "SYS/SYSTEM/PDBADMIN (database admin)"
    $rsp = Join-Path $env:TEMP ("oracle_free_{0}.rsp" -f ([guid]::NewGuid()))
    $log = Join-Path $env:TEMP "oracle_free_install.log"
    try {
        @(
            "INSTALLDIR=$InstallDir",
            "PASSWORD=$sysPwd",
            "LISTENER_PORT=$ListenerPort",
            "CHAR_SET=AL32UTF8",
            "DB_DOMAIN=",
            "SOFTWARE_ONLY=FALSE",
            "MODIFY_DIRECTORY_PERMISSIONS=TRUE"
        ) | Set-Content -Path $rsp -Encoding ascii
        Write-Host "Installing Oracle Database Free (typically 15-40 minutes)..." -ForegroundColor Cyan
        $setupArgs = "/s /v`"RSP_FILE=$rsp`" /v`"/L*v $log`" /v`"/qn`""
        $proc = Start-Process -FilePath $setup -ArgumentList $setupArgs -Wait -PassThru
        Write-Host "Installer exit code: $($proc.ExitCode)  (log: $log)"
        if ($proc.ExitCode -ne 0) { throw "Installer failed. Inspect $log" }
    } finally {
        if (Test-Path $rsp) { Remove-Item $rsp -Force }   # never leave the admin password on disk
    }
}

# ---------------------------------------------------------------- 2. Locate sqlplus
$sqlplus = Get-ChildItem -Path $InstallDir -Recurse -Filter sqlplus.exe -ErrorAction SilentlyContinue |
           Select-Object -First 1 -ExpandProperty FullName
if (-not $sqlplus) { throw "sqlplus.exe not found under $InstallDir" }
Write-Host "sqlplus: $sqlplus"

# ---------------------------------------------------------------- 3. Create app schema in FREEPDB1
$appPwd = Read-ConfirmedPassword "$AppUser (project schema)"
$ddl = @"
WHENEVER SQLERROR EXIT SQL.SQLCODE
ALTER SESSION SET CONTAINER = FREEPDB1;
DECLARE n NUMBER;
BEGIN
  SELECT COUNT(*) INTO n FROM dba_users WHERE username = '$AppUser';
  IF n = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER $AppUser IDENTIFIED BY "$appPwd" DEFAULT TABLESPACE USERS QUOTA 2G ON USERS';
  ELSE
    EXECUTE IMMEDIATE 'ALTER USER $AppUser IDENTIFIED BY "$appPwd"';
  END IF;
END;
/
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE, CREATE PROCEDURE TO $AppUser;
EXIT
"@
# Script is piped through stdin with OS authentication (/ as sysdba), so no password is on the command line.
$env:ORACLE_SID = "FREE"
$ddl | & $sqlplus -S -L "/ as sysdba"
if ($LASTEXITCODE -ne 0) { throw "Schema creation failed (sqlplus exit $LASTEXITCODE)." }
Write-Host "Schema $AppUser ready in FREEPDB1." -ForegroundColor Green

# ---------------------------------------------------------------- 4. Store credentials
$dsn = "localhost:$ListenerPort/FREEPDB1"
[Environment]::SetEnvironmentVariable("ORACLE_USER", $AppUser, "User")
[Environment]::SetEnvironmentVariable("ORACLE_DSN",  $dsn,     "User")

$py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path $py) {
    # Password goes through stdin, not argv.
    $appPwd | & $py -c "import sys,keyring; keyring.set_password('hrra_oracle', sys.argv[1], sys.stdin.readline().rstrip('\r\n'))" $AppUser
    if ($LASTEXITCODE -eq 0) { Write-Host "Password stored in Windows Credential Manager (service 'hrra_oracle')." -ForegroundColor Green }
} else {
    Write-Warning "Project venv not found; store the password later with:  python -m keyring set hrra_oracle $AppUser"
}

$sysPwd = $null; $appPwd = $null
Write-Host "`nDone. Open a NEW terminal so ORACLE_USER / ORACLE_DSN are visible, then run:" -ForegroundColor Cyan
Write-Host "  .venv\Scripts\python -m src.oracle_loader --check"
