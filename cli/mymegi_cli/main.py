import json
import mimetypes
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer


app = typer.Typer(help="My Megi command line client.", no_args_is_help=True)
contacts_app = typer.Typer(help="Search and inspect contacts.", no_args_is_help=True)
notes_app = typer.Typer(help="Manage relationship notes.", no_args_is_help=True)
app.add_typer(contacts_app, name="contacts")
app.add_typer(notes_app, name="notes")


@app.callback()
def main() -> None:
    """Operate My Megi through its HTTP API."""


def api_url() -> str:
    return os.getenv("MYMEGI_API_URL", "http://localhost:8000").rstrip("/")


def request_headers() -> dict[str, str]:
    token = os.getenv("MYMEGI_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def dump_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(
            method,
            f"{api_url()}{path}",
            headers={**request_headers(), **kwargs.pop("headers", {})},
            timeout=60,
            **kwargs,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        try:
            payload = exc.response.json()
            detail = payload.get("detail") or payload.get("message") or detail
        except ValueError:
            pass
        typer.echo(f"API error {exc.response.status_code}: {detail}", err=True)
        raise typer.Exit(1) from exc
    except httpx.RequestError as exc:
        typer.echo(f"Connection error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not response.content:
        return {}
    return response.json()


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def print_contact_row(item: dict[str, Any]) -> None:
    company = item.get("company") or item.get("companyEnglishName") or "-"
    title = item.get("title") or "-"
    typer.echo(f"{item.get('id')}  {item.get('name') or '-'}  {company}  {title}")


@app.command()
def health(
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Check API health."""
    payload = request_json("GET", "/health")
    if json_output:
        dump_json(payload)
        return
    typer.echo(f"Status: {payload.get('status')}")
    typer.echo(f"Database: {payload.get('database')}")
    typer.echo(f"OCR: {payload.get('ocrEngine')}")
    typer.echo(f"LLM: {payload.get('llmModel')}")


@app.command()
def upload(
    file: Annotated[Path, typer.Argument(help="Front-side business card image or PDF.")],
    back_file: Annotated[
        Path | None,
        typer.Option("--back-file", "--back", help="Optional back-side image or PDF."),
    ] = None,
    met_at: Annotated[str | None, typer.Option("--met-at", help="Where/how you met.")] = None,
    met_on: Annotated[str | None, typer.Option("--met-on", help="Meeting date, YYYY-MM-DD.")] = None,
    note: Annotated[str | None, typer.Option("--note", help="Relationship note.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Upload a card and trigger recognition."""
    if not file.is_file():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(1)
    if back_file and not back_file.is_file():
        typer.echo(f"Back file not found: {back_file}", err=True)
        raise typer.Exit(1)

    data = {
        key: value
        for key, value in {"metAt": met_at, "metOn": met_on, "note": note}.items()
        if value is not None
    }
    with ExitStack() as stack:
        front_handle = stack.enter_context(file.open("rb"))
        files: dict[str, tuple[str, Any, str]] = {
            "file": (
                file.name,
                front_handle,
                mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            )
        }
        if back_file:
            back_handle = stack.enter_context(back_file.open("rb"))
            files["backFile"] = (
                back_file.name,
                back_handle,
                mimetypes.guess_type(back_file.name)[0] or "application/octet-stream",
            )
        payload = request_json("POST", "/api/cards/upload", data=data, files=files)

    if json_output:
        dump_json(payload)
        return
    typer.echo(f"Uploaded: {payload.get('cardId')}")
    typer.echo(f"Status: {payload.get('status')}")
    typer.echo(f"File: {payload.get('fileName')}")
    if payload.get("backFileName"):
        typer.echo(f"Back file: {payload.get('backFileName')}")
    if payload.get("confidence") is not None:
        typer.echo(f"Confidence: {round(float(payload['confidence']) * 100)}%")
    if payload.get("autoConfirmed"):
        typer.echo("Auto confirmed: yes")


@contacts_app.command("search")
def contacts_search(
    q: Annotated[str | None, typer.Option("--q", help="Keyword across name, company, email, phone.")] = None,
    company: Annotated[str | None, typer.Option("--company", help="Company keyword.")] = None,
    company_classification: Annotated[
        str | None,
        typer.Option("--company-classification", help="Company classification."),
    ] = None,
    region_classification: Annotated[
        str | None,
        typer.Option("--region-classification", help="Region classification."),
    ] = None,
    industry: Annotated[
        str | None,
        typer.Option("--industry", help="Industry classification."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Search contacts."""
    params = {
        "q": q or company,
        "companyClassification": company_classification,
        "regionClassification": region_classification,
        "industryClassification": industry,
        "limit": limit,
        "offset": offset,
    }
    payload = request_json(
        "GET",
        "/api/contacts",
        params={key: value for key, value in params.items() if value not in (None, "")},
    )
    if json_output:
        dump_json(payload)
        return
    items = payload.get("items", [])
    typer.echo(f"Total: {payload.get('total', len(items))}")
    for item in items:
        print_contact_row(item)


@contacts_app.command("show")
def contacts_show(
    contact_id: Annotated[str, typer.Argument(help="Contact id.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Show a contact."""
    payload = request_json("GET", f"/api/contacts/{contact_id}")
    if json_output:
        dump_json(payload)
        return

    typer.echo(f"ID: {payload.get('id')}")
    typer.echo(f"Name: {payload.get('name') or '-'}")
    if payload.get("englishName"):
        typer.echo(f"English name: {payload.get('englishName')}")
    typer.echo(f"Title: {payload.get('title') or '-'}")
    company = payload.get("company") or {}
    typer.echo(f"Company: {company.get('name') or company.get('englishName') or '-'}")
    if company.get("industry"):
        typer.echo(f"Industry: {company.get('industry')}")
    methods = payload.get("methods") or []
    if methods:
        typer.echo("Methods:")
        for method in methods:
            typer.echo(f"  - {method.get('type')}: {method.get('value')}")
    addresses = payload.get("addresses") or []
    if addresses:
        typer.echo("Addresses:")
        for address in addresses:
            typer.echo(f"  - {address.get('raw') or address.get('english')}")
    notes = payload.get("relationshipNotes") or []
    if notes:
        typer.echo("Relationship notes:")
        for note in notes:
            when = note.get("metOn") or note.get("createdAt") or ""
            typer.echo(f"  - {when}: {note.get('summary') or '-'}")


@notes_app.command("add")
def notes_add(
    contact_id: Annotated[str, typer.Argument(help="Contact id.")],
    text: Annotated[str, typer.Option("--text", help="Relationship note text.")],
    met_at: Annotated[str | None, typer.Option("--met-at", help="Where/how you met.")] = None,
    met_on: Annotated[str | None, typer.Option("--met-on", help="Meeting date, YYYY-MM-DD.")] = None,
    next_action: Annotated[str | None, typer.Option("--next-action", help="Next action.")] = None,
    next_action_due_on: Annotated[
        str | None,
        typer.Option("--next-action-due-on", help="Next action due date, YYYY-MM-DD."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print raw JSON response.")] = False,
) -> None:
    """Add a relationship note."""
    payload = request_json(
        "POST",
        f"/api/contacts/{contact_id}/notes",
        json={
            "summary": text,
            "metAt": met_at,
            "metOn": met_on,
            "nextAction": next_action,
            "nextActionDueOn": next_action_due_on,
        },
    )
    if json_output:
        dump_json(payload)
        return
    typer.echo(f"Added note: {payload.get('id')}")
    typer.echo(f"Contact: {payload.get('contactId')}")
    typer.echo(f"Summary: {payload.get('summary')}")


if __name__ == "__main__":
    app()
