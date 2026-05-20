# PowerShell task runner. Usage: .\make.ps1 <target>
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "dev", "test", "fmt", "lint")]
    [string]$Target = "dev"
)

$ErrorActionPreference = "Stop"
$venvActivate = ".\.venv\Scripts\Activate.ps1"

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
    }
    "dev" {
        Use-Venv
        uvicorn app.main:app --reload --app-dir backend --port 8000
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
}
