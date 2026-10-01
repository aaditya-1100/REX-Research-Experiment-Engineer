"""REX Command Line Interface (CLI).

Provides terminal commands for formal research verification (REX-026) and
experiment reproduction (REX-027).
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rex.evidence.reproduce import ExperimentReproducer, ReproducibilityStatus, ReproductionOutcome
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.persistence.database import create_db_engine, create_session_factory

cli = typer.Typer(
    name="rex",
    help="REX — Research Experiment Engineer: Formal verification and reproducibility CLI.",
    no_args_is_help=True,
)
console = Console()


@cli.command("version")
def version_cmd() -> None:
    """Print the REX engine version and active batches."""
    console.print(
        "[bold cyan]REX — Research Experiment Engineer[/bold cyan] [bold green]v0.4.0[/bold green]"
    )
    console.print(
        "Approved Batches: 1 (Execution), 2 (Intelligence), 3 (Experimental), 4 (Evidence)"
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


if __name__ == "__main__":
    cli()
