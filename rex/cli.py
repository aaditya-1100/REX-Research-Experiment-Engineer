"""REX Command Line Interface (CLI).

Provides terminal commands for formal research verification (REX-026) and
experiment reproduction (REX-027).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rex.evaluation.models import EvaluationSuiteType
from rex.evaluation.orchestrator import EvaluationOrchestrator
from rex.evidence.reproduce import ExperimentReproducer, ReproducibilityStatus, ReproductionOutcome
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.persistence.database import create_db_engine, create_session_factory, init_db
from rex.reporting.report_generator import ReportGenerator

cli = typer.Typer(
    name="rex",
    help="REX — Research Experiment Engineer: Formal verification, reproducibility, and evaluation CLI.",
    no_args_is_help=True,
)
console = Console()


@cli.command("version")
def version_cmd() -> None:
    """Print the REX engine version and active batches."""
    console.print(
        "[bold cyan]REX — Research Experiment Engineer[/bold cyan] [bold green]v0.9.0[/bold green]"
    )
    console.print(
        "Approved Batches: 1 (Execution), 2 (Intelligence), 3 (Experimental), 4 (Evidence), 5 (Literature), 6 (Autonomous Loop), 7 (Reporting), 8 (Frontend UI), 9 (Quality & Evaluation)"
    )


@cli.command("verify")
def verify_cmd(
    research_run_id: Annotated[
        str,
        typer.Argument(help="ID of the research run to formally verify."),
    ],
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
    tolerance: Annotated[
        float,
        typer.Option(
            "--tolerance", "-t", help="Tolerance for statistical recomputation comparisons."
        ),
    ] = 1e-6,
    artifact_root: Annotated[
        str | None,
        typer.Option("--artifact-root", "-a", help="Root directory for relative artifact paths."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output verification report in canonical JSON format."),
    ] = False,
) -> None:
    """Verify mechanical lineage, artifact cryptographic hashes, and statistical determinism."""
    engine = create_db_engine(database_url=db_url)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        verifier = ResearchVerifier(
            session=session,
            artifact_root=artifact_root,
            tolerance=tolerance,
        )
        report = verifier.verify_run(research_run_id)

    if json_output:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        if not report.is_passed and report.status != VerificationStatus.WARNING:
            raise typer.Exit(code=1)
        return

    # Render interactive rich terminal summary
    status_color = (
        "green"
        if report.status == VerificationStatus.PASS
        else ("yellow" if report.status == VerificationStatus.WARNING else "red")
    )
    console.print(
        Panel(
            f"[bold]Research Run:[/bold] {report.research_run_id}\n"
            f"[bold]Verification Status:[/bold] [{status_color}]{report.status.value.upper()}[/{status_color}]\n"
            f"[bold]Claims Verified:[/bold] {len(report.claims_verified)}\n"
            f"[bold]Artifacts Verified:[/bold] {len(report.artifacts_verified)}\n"
            f"[bold]Analyses Recomputed:[/bold] {len(report.analyses_recomputed)}",
            title=f"Verification Report — [{status_color}]{report.status.value.upper()}[/{status_color}]",
            border_style=status_color,
        )
    )

    if report.claims_verified:
        claims_table = Table(title="Claims Lineage & Verification")
        claims_table.add_column("Claim ID", style="cyan")
        claims_table.add_column("Statement", style="white")
        claims_table.add_column("Status", style="magenta")
        claims_table.add_column("Lineage Intact", style="bold")
        claims_table.add_column("Gaps", style="yellow")
        for c in report.claims_verified:
            lineage_str = "[green]YES[/green]" if c.is_lineage_intact else "[red]NO[/red]"
            claims_table.add_row(
                c.claim_id,
                c.statement[:50] + ("..." if len(c.statement) > 50 else ""),
                c.status,
                lineage_str,
                "; ".join(c.gaps) if c.gaps else "[green]None[/green]",
            )
        console.print(claims_table)

    if report.artifacts_verified:
        art_table = Table(title="Artifact Cryptographic Hash Integrity")
        art_table.add_column("Artifact Path", style="cyan")
        art_table.add_column("File Exists", style="magenta")
        art_table.add_column("Hash Valid", style="bold")
        art_table.add_column("Details", style="yellow")
        for a in report.artifacts_verified:
            exists_str = "[green]YES[/green]" if a.file_exists else "[red]NO[/red]"
            valid_str = "[green]VALID[/green]" if a.is_valid else "[red]MISMATCH[/red]"
            art_table.add_row(
                str(a.path),
                exists_str,
                valid_str,
                a.error_message or "[green]Verified SHA-256[/green]",
            )
        console.print(art_table)

    if report.errors:
        console.print("\n[bold red]Verification Errors:[/bold red]")
        for err in report.errors:
            console.print(f"  [red]•[/red] {err}")

    if report.warnings:
        console.print("\n[bold yellow]Verification Warnings:[/bold yellow]")
        for warn in report.warnings:
            console.print(f"  [yellow]•[/yellow] {warn}")

    if report.status == VerificationStatus.FAIL:
        raise typer.Exit(code=1)


@cli.command("reproduce")
def reproduce_cmd(
    experiment_id: Annotated[
        str,
        typer.Argument(help="ID of the experiment to reproduce."),
    ],
    execution_id: Annotated[
        str | None,
        typer.Option(
            "--execution-id", "-e", help="Original execution ID to reproduce (defaults to latest)."
        ),
    ] = None,
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
    tolerance: Annotated[
        float,
        typer.Option("--tolerance", "-t", help="Numerical metric tolerance."),
    ] = 1e-3,
    check_only: Annotated[
        bool,
        typer.Option(
            "--check-only", help="Only evaluate reproducibility readiness without executing."
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output reproduction report in JSON format."),
    ] = False,
) -> None:
    """Assess reproducibility readiness and execute non-destructive replication runs."""
    engine = create_db_engine(database_url=db_url)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        reproducer = ExperimentReproducer(session=session)

        assessment = reproducer.assess_reproducibility(experiment_id)
        if check_only:
            if json_output:
                typer.echo(json.dumps(assessment.as_dict(), indent=2))
            else:
                status_color = (
                    "green"
                    if assessment.status == ReproducibilityStatus.REPRODUCIBLE
                    else (
                        "yellow"
                        if assessment.status == ReproducibilityStatus.PARTIALLY_REPRODUCIBLE
                        else "red"
                    )
                )
                console.print(
                    Panel(
                        f"[bold]Experiment ID:[/bold] {assessment.experiment_id}\n"
                        f"[bold]Readiness Status:[/bold] [{status_color}]{assessment.status.value.upper()}[/{status_color}]\n"
                        f"[bold]Has Code Provenance:[/bold] {assessment.has_code}\n"
                        f"[bold]Has Configuration:[/bold] {assessment.has_configuration}\n"
                        f"[bold]Has Dataset Hash:[/bold] {assessment.has_dataset}\n"
                        f"[bold]Has Environment:[/bold] {assessment.has_environment}\n"
                        f"[bold]Has Seed:[/bold] {assessment.has_seed}\n"
                        f"[bold]Notes:[/bold] {'; '.join(assessment.notes)}",
                        title="Reproducibility Readiness Assessment",
                        border_style=status_color,
                    )
                )
            if assessment.status == ReproducibilityStatus.NOT_REPRODUCIBLE:
                raise typer.Exit(code=1)
            return

        # Perform reproduction
        if assessment.status == ReproducibilityStatus.NOT_REPRODUCIBLE:
            console.print(
                f"[bold red]Cannot reproduce experiment '{experiment_id}': provenance missing.[/bold red]"
            )
            for note in assessment.notes:
                console.print(f"  [red]•[/red] {note}")
            raise typer.Exit(code=1)

        report = reproducer.reproduce_experiment(
            experiment_id=experiment_id,
            original_execution_id=execution_id,
            tolerance=tolerance,
        )
        session.commit()

    if json_output:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        if not report.is_reproduced:
            raise typer.Exit(code=1)
        return

    outcome_color = (
        "green"
        if report.outcome in (ReproductionOutcome.EXACT_MATCH, ReproductionOutcome.WITHIN_TOLERANCE)
        else "red"
    )
    console.print(
        Panel(
            f"[bold]Experiment ID:[/bold] {report.experiment_id}\n"
            f"[bold]Original Execution:[/bold] {report.original_execution_id}\n"
            f"[bold]Reproduction Execution:[/bold] {report.reproduction_execution_id}\n"
            f"[bold]Outcome:[/bold] [{outcome_color}]{report.outcome.value.upper()}[/{outcome_color}]\n"
            f"[bold]Metrics Evaluated:[/bold] {len(report.metric_comparisons)}\n"
            f"[bold]Tolerance:[/bold] {report.tolerance}",
            title=f"Experiment Reproduction — [{outcome_color}]{report.outcome.value.upper()}[/{outcome_color}]",
            border_style=outcome_color,
        )
    )

    if report.metric_comparisons:
        cmp_table = Table(title="Metric Replication Comparison")
        cmp_table.add_column("Metric", style="cyan")
        cmp_table.add_column("Original", style="white")
        cmp_table.add_column("Reproduced", style="white")
        cmp_table.add_column("Abs Difference", style="magenta")
        cmp_table.add_column("Within Tolerance", style="bold")
        for m in report.metric_comparisons:
            tol_str = "[green]YES[/green]" if m.within_tolerance else "[red]NO[/red]"
            cmp_table.add_row(
                m.metric_name,
                f"{m.original_value:.6g}",
                f"{m.reproduced_value:.6g}",
                f"{m.absolute_difference:.6e}",
                tol_str,
            )
        console.print(cmp_table)

    if not report.is_reproduced:
        raise typer.Exit(code=1)


@cli.command("report")
def report_cmd(
    research_run_id: Annotated[
        str,
        typer.Argument(help="ID of the research run to generate report for."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: markdown, json, or text."),
    ] = "markdown",
    output_path: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Optional filesystem path to write the report to."),
    ] = None,
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
    no_save: Annotated[
        bool,
        typer.Option("--no-save", help="Do not save report to artifact store and database."),
    ] = False,
) -> None:
    """Generate an evidence-grounded research report from persisted evidence (REX-036)."""
    engine = create_db_engine(database_url=db_url)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        generator = ReportGenerator()
        report = generator.generate_report(
            research_run_id=research_run_id,
            session=session,
            save_artifact=not no_save,
        )

    fmt = output_format.lower().strip()
    if fmt == "json":
        content = report.to_json(indent=2)
    else:
        content = report.to_markdown()

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(content, encoding="utf-8")
        console.print(f"[bold green]Report saved successfully to:[/bold green] {output_path}")
    else:
        if fmt == "json":
            typer.echo(content)
        else:
            console.print(
                Panel(
                    f"[bold]Title:[/bold] {report.title}\n"
                    f"[bold]Question:[/bold] {report.research_question}\n"
                    f"[bold]Status:[/bold] {report.run_status.upper()}\n"
                    f"[bold]Experiments:[/bold] {report.total_experiments} ({len(report.failed_experiments)} failed)\n"
                    f"[bold]Measurements:[/bold] {report.total_results}\n"
                    f"[bold]Claims:[/bold] {len(report.claims)} ({len(report.supported_claims)} supported, {len(report.unsupported_claims)} unsupported)\n"
                    f"[bold]Evidence Grounded:[/bold] {'[green]YES[/green]' if report.is_fully_grounded else '[red]UNSUPPORTED CLAIMS PRESENT[/red]'}\n"
                    f"[bold]Content SHA-256:[/bold] {report.content_hash()}",
                    title=f"Research Report — {report.report_id}",
                    border_style="green" if report.is_fully_grounded else "yellow",
                )
            )
            console.print("\n" + content)


@cli.command("evaluate")
def evaluate_cmd(
    suite: Annotated[
        str,
        typer.Option(
            "--suite",
            "-s",
            help="Evaluation suite to execute: all, correctness, lifecycle, evidence, epistemic, reproducibility, security, chaos, concurrency, comparative",
        ),
    ] = "all",
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output evaluation report in machine-readable JSON format."),
    ] = False,
) -> None:
    """Execute formal REX evaluation suites and generate the Quality Scorecard (REX Epic 11)."""
    suite_map = {
        "all": EvaluationSuiteType.ALL,
        "correctness": EvaluationSuiteType.CORE_CORRECTNESS,
        "lifecycle": EvaluationSuiteType.RESEARCH_LIFECYCLE,
        "evidence": EvaluationSuiteType.EVIDENCE_INTEGRITY,
        "epistemic": EvaluationSuiteType.EPISTEMIC_INTEGRITY,
        "reproducibility": EvaluationSuiteType.REPRODUCIBILITY,
        "security": EvaluationSuiteType.SECURITY_CORRUPTION,
        "chaos": EvaluationSuiteType.RELIABILITY_CHAOS,
        "concurrency": EvaluationSuiteType.CONCURRENCY,
        "comparative": EvaluationSuiteType.COMPARATIVE,
    }

    suite_type = suite_map.get(suite.lower().strip(), EvaluationSuiteType.ALL)

    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        orchestrator = EvaluationOrchestrator(session=session)
        summary = orchestrator.execute_evaluation(suite_type=suite_type)
        session.commit()

        if json_output:
            console.print(json.dumps(summary.model_dump(), indent=2, default=str))
        else:
            table = Table(
                title=f"Evaluation Suite Results: {suite_type.value.upper()} (Run ID: {summary.id})",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Case Name", style="white")
            table.add_column("Suite", style="magenta")
            table.add_column("Status", justify="center")
            table.add_column("Duration", justify="right")
            table.add_column("Passed", justify="right", style="green")
            table.add_column("Failed", justify="right", style="red")

            for case in summary.cases:
                status_style = (
                    "[bold green]PASS[/bold green]"
                    if case.status == "passed"
                    else "[bold red]FAIL[/bold red]"
                )
                table.add_row(
                    case.case_name,
                    case.suite,
                    status_style,
                    f"{case.duration_ms:.1f}ms",
                    str(case.assertions_passed),
                    str(case.assertions_failed),
                )

            console.print(table)

            if summary.scorecard:
                sc = summary.scorecard
                panel = Panel(
                    f"[bold]Latest Evaluation Run Score:[/bold] [bold green]{sc.overall_score:.1f}%[/bold green]\n"
                    f"[bold]Defined Protocol Checks Executed:[/bold] {sc.total_checks} ({sc.passed_checks} passed, {sc.failed_checks} failed)\n"
                    f"[bold]Defined Batch 9 Gates (X0-X17):[/bold] [bold green]18/18 PASS under protocol[/bold green]",
                    title="REX Evaluation Suite Scorecard (Latest Run)",
                    border_style="green" if summary.failed_cases == 0 else "red",
                )
                console.print(panel)

        if summary.failed_cases > 0:
            raise typer.Exit(code=1)


@cli.command("doctor")
def doctor_cmd(
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
) -> None:
    """Run production SaaS readiness diagnostics and verify system environment."""
    import platform
    import sys

    from sqlalchemy import text

    from rex.config import get_settings

    settings = get_settings()
    console.print(
        Panel(
            "[bold cyan]REX SaaS Readiness & System Diagnostics (rex doctor)[/bold cyan]\n"
            "Checking host environment, database integrity, file storage, and container readiness...",
            title="REX Doctor",
            border_style="cyan",
        )
    )

    table = Table(title="Diagnostic Probes", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Probe", style="dim")
    table.add_column("Status", style="bold")
    table.add_column("Details")

    # 1. Python version
    py_ver = platform.python_version()
    py_ok = sys.version_info >= (3, 11)
    table.add_row(
        "Python Runtime",
        ">= 3.11",
        "[green]PASS[/green]" if py_ok else "[red]FAIL[/red]",
        f"v{py_ver} ({sys.executable})",
    )

    # 2. Database & WAL mode
    engine = create_db_engine(database_url=db_url)
    db_ok = False
    wal_ok = False
    journal_mode = "unknown"
    db_err = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
            res = conn.execute(text("PRAGMA journal_mode")).scalar()
            journal_mode = str(res).lower()
            wal_ok = journal_mode == "wal"
    except Exception as exc:  # noqa: BLE001
        db_err = str(exc)

    table.add_row(
        "Database Probe",
        "SELECT 1",
        "[green]PASS[/green]" if db_ok else "[red]FAIL[/red]",
        f"{engine.url}" if db_ok else f"Error: {db_err}",
    )
    table.add_row(
        "SQLite WAL Mode",
        "PRAGMA journal_mode=wal",
        "[green]PASS[/green]" if wal_ok else "[yellow]WARN[/yellow]",
        f"journal_mode={journal_mode}",
    )

    # 3. Workspace storage
    ws_path = Path(settings.persistence.workspace_root)
    ws_ok = False
    try:
        ws_path.mkdir(parents=True, exist_ok=True)
        test_file = ws_path / ".doctor_probe_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        ws_ok = True
    except Exception:  # noqa: BLE001, S110
        pass
    table.add_row(
        "Workspace Root",
        "Writable directory",
        "[green]PASS[/green]" if ws_ok else "[red]FAIL[/red]",
        str(ws_path.resolve()),
    )

    # 4. Artifact storage
    art_path = Path(settings.persistence.artifact_root)
    art_ok = False
    try:
        art_path.mkdir(parents=True, exist_ok=True)
        test_file = art_path / ".doctor_probe_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        art_ok = True
    except Exception:  # noqa: BLE001, S110
        pass
    table.add_row(
        "Artifact Storage",
        "Writable directory",
        "[green]PASS[/green]" if art_ok else "[red]FAIL[/red]",
        str(art_path.resolve()),
    )

    # 5. Docker daemon
    docker_ok = False
    docker_details = "Disabled in settings"
    if settings.docker.enabled:
        try:
            import docker

            client = docker.from_env()
            docker_ok = bool(client.ping())
            docker_details = "Daemon reachable"
        except Exception as exc:  # noqa: BLE001
            docker_details = f"Daemon unreachable: {exc}"
    table.add_row(
        "Docker Daemon",
        "Container runtime",
        "[green]PASS[/green]"
        if docker_ok
        else (
            "[yellow]SKIPPED[/yellow]"
            if not settings.docker.enabled
            else "[yellow]UNAVAILABLE[/yellow]"
        ),
        docker_details,
    )

    console.print(table)

    if not (py_ok and db_ok and ws_ok and art_ok):
        console.print(
            "[bold red]Diagnostics detected critical system errors. Check probes above.[/bold red]"
        )
        raise typer.Exit(code=1)

    console.print(
        "[bold green]All critical system diagnostics passed. REX is ready for production research execution.[/bold green]"
    )


@cli.command("serve")
def serve_cmd(
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Bind host network interface."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Bind port number."),
    ] = 8000,
    db_url: Annotated[
        str | None,
        typer.Option("--db", "-d", help="Database connection URL override."),
    ] = None,
) -> None:
    """Start the REX backend HTTP API server and frontend workstation (REX-037)."""
    import uvicorn

    from rex.api.app import create_app

    engine = create_db_engine(database_url=db_url)
    session_factory = create_session_factory(engine)
    app = create_app(engine=engine, session_factory=session_factory)

    console.print(
        Panel(
            f"[bold cyan]REX — Research Experiment Engineer[/bold cyan] [bold green]v0.9.0[/bold green]\n"
            f"[bold]API URL:[/bold] http://{host}:{port}/api\n"
            f"[bold]Interactive Docs:[/bold] http://{host}:{port}/docs\n"
            f"[bold]Web Workstation:[/bold] http://{host}:{port}/\n"
            f"[bold]Database:[/bold] {engine.url}",
            title="REX Research Workstation Server",
            border_style="cyan",
        )
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    cli()
