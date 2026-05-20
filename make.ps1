# PowerShell task runner. Usage: .\make.ps1 <target>
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "dev", "backend", "frontend", "test", "fmt", "lint", "build")]
    [string]$Target = "dev"
)

$ErrorActionPreference = "Stop"
$venvActivate = ".\.venv\Scripts\Activate.ps1"

# Node is installed at C:\nodejs on this machine; add it to PATH locally so
# `ng` / `npm` work regardless of how the shell was launched.
function Use-Node {
    if (-not (Test-Path "C:\nodejs\node.exe")) {
        throw "Node not found at C:\nodejs. Install Node 20+ or adjust make.ps1."
    }
    if ($env:Path -notlike "*C:\nodejs*") {
        $env:Path = "C:\nodejs;" + $env:Path
    }
}

function Use-Venv {
    if (-not (Test-Path ".venv")) {
        throw "No .venv found. Run: .\make.ps1 install"
    }
    & $venvActivate
}

switch ($Target) {
    "install" {
        if (-not (Test-Path ".venv")) {
            python -m venv .venv
        }
        & $venvActivate
        pip install -e ".[dev]"
        Use-Node
        Push-Location frontend
        try { npm install } finally { Pop-Location }
    }
    "dev" {
        # Backend in foreground; user runs `.\make.ps1 frontend` in a second
        # terminal. Trying to background both from the same script gets messy
        # because Ctrl-C only stops the foreground process.
        Use-Venv
        Write-Host "Backend on :8000. In a second terminal, run: .\make.ps1 frontend"
        uvicorn app.main:app --reload --app-dir backend --port 8000
    }
    "backend" {
        Use-Venv
        uvicorn app.main:app --reload --app-dir backend --port 8000
    }
    "frontend" {
        Use-Node
        Push-Location frontend
        try { ng serve --port 4200 } finally { Pop-Location }
    }
    "test" {
        Use-Venv
        pytest
    }
    "fmt" {
        Use-Venv
        ruff format backend
    }
    "lint" {
        Use-Venv
        ruff check backend
    }
    "build" {
        Use-Node
        Push-Location frontend
        try { ng build } finally { Pop-Location }
    }
}
