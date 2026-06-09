import os
from typing import Annotated

import httpx
import typer


app = typer.Typer(help="My Megi command line client.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Operate My Megi through its HTTP API."""


def api_url() -> str:
    return os.getenv("MYMEGI_API_URL", "http://localhost:8000").rstrip("/")


@app.command()
def health(
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Check API health."""
    response = httpx.get(f"{api_url()}/health", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if json_output:
        typer.echo(payload)
        return
    typer.echo(f"Status: {payload.get('status')}")
    typer.echo(f"Database: {payload.get('database')}")
    typer.echo(f"OCR: {payload.get('ocrEngine')}")
    typer.echo(f"LLM: {payload.get('llmModel')}")


if __name__ == "__main__":
    app()
