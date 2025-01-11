import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import tqdm
from litellm import model_cost
from rich import box
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

logger = logging.getLogger(__name__)


@dataclass
class OnlineStatusTracker:
    """Tracks the status of all requests."""

    num_tasks_started: int = 0
    num_tasks_in_progress: int = 0
    num_tasks_succeeded: int = 0
    num_tasks_failed: int = 0
    num_tasks_already_completed: int = 0
    num_api_errors: int = 0
    num_other_errors: int = 0
    num_rate_limit_errors: int = 0
    available_request_capacity: float = 1.0
    available_token_capacity: float = 0
    last_update_time: float = field(default_factory=time.time)
    max_requests_per_minute: int = 0
    max_tokens_per_minute: int = 0
    pbar: tqdm = field(default=None)
    response_cost: float = 0
    time_of_last_rate_limit_error: float = field(default=0.0)

    # Stats tracking
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0

    # Cost per million tokens
    input_cost_per_million: Optional[float] = None
    output_cost_per_million: Optional[float] = None

    start_time: float = field(default_factory=time.time, init=False)

    # Add model name field
    model: str = ""

    def start_display(self, total_requests: int, model: str):
        """Start status display."""
        self.total_requests = total_requests
        self.model = model  # Store the model name

        self._progress = Progress(
            TextColumn(
                "[cyan]{task.description}[/cyan]\n"
                "{task.fields[status_text]}\n"
                "{task.fields[token_text]}\n"
                "{task.fields[cost_text]}\n"
                "{task.fields[rate_limit_text]}\n"
                "{task.fields[price_text]}",
                justify="left",
            ),
            TextColumn("\n\n\n\n\n\n"),  # Spacer
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("[bold white]•[/bold white]"),
            TimeElapsedColumn(),
            TextColumn("[bold white]•[/bold white]"),
            TimeRemainingColumn(),
        )
        self._task_id = self._progress.add_task(
            description=f"[cyan]Generating data for model {model}",
            total=total_requests,
            completed=0,
            status_text="[bold white]Status:[/bold white] [dim]Initializing...[/dim]",
            token_text="[bold white]Tokens:[/bold white] --",
            cost_text="[bold white]Cost:[/bold white] --",
            model_name_text="[bold white]Model:[/bold white] --",
            rate_limit_text="[bold white]Rate Limits:[/bold white] --",
            price_text="[bold white]Model Pricing:[/bold white] --",
        )
        if model in model_cost:
            self.input_cost_per_million = model_cost[model]["input_cost_per_token"] * 1_000_000
            self.output_cost_per_million = model_cost[model]["output_cost_per_token"] * 1_000_000

    def update_display(self):
        """Updates the progress display."""
        avg_prompt = self.total_prompt_tokens / max(1, self.num_tasks_succeeded)
        avg_completion = self.total_completion_tokens / max(1, self.num_tasks_succeeded)
        avg_cost = self.total_cost / max(1, self.num_tasks_succeeded)
        projected_cost = avg_cost * self.total_requests

        # Calculate current rpm
        elapsed_minutes = (time.time() - self.start_time) / 60
        current_rpm = self.num_tasks_succeeded / elapsed_minutes if elapsed_minutes > 0 else 0

        # Format the text for each line
        status_text = (
            "[bold white]Status:[/bold white] Processing "
            f"[dim]([green]✓{self.num_tasks_succeeded}[/green] "
            f"[red]✗{self.num_tasks_failed}[/red] "
            f"[yellow]⋯{self.num_tasks_in_progress}[/yellow] "
            f"[dim]({current_rpm:.1f} rpm)[/dim]"
        )

        token_text = f"[bold white]Tokens:[/bold white] Avg Input: [blue]{avg_prompt:.0f}[/blue] • Avg Output: [blue]{avg_completion:.0f}[/blue]"

        cost_text = (
            "[bold white]Cost:[/bold white] "
            f"Current: [magenta]${self.total_cost:.3f}[/magenta] • "
            f"Projected: [magenta]${projected_cost:.3f}[/magenta] • "
            f"Rate: [magenta]${self.total_cost / max(1, self.num_tasks_succeeded):.3f}/min[/magenta]"
        )
        model_name_text = f"[bold white]Model:[/bold white] [blue]{self.model}[/blue]"
        rate_limit_text = (
            f"[bold white]Rate Limits:[/bold white] rpm: [blue]{self.max_requests_per_minute}[/blue] • tpm: [blue]{self.max_tokens_per_minute}[/blue]"
        )
        input_cost_str = f"${self.input_cost_per_million:.3f}" if isinstance(self.input_cost_per_million, float) else "N/A"
        output_cost_str = f"${self.output_cost_per_million:.3f}" if isinstance(self.output_cost_per_million, float) else "N/A"

        price_text = f"[bold white]Model Pricing:[/bold white] Per 1M tokens: Input: [red]{input_cost_str}[/red] • Output: [red]{output_cost_str}[/red]"

        # Update the progress display
        self._progress.update(
            self._task_id,
            advance=1,
            completed=self.num_tasks_succeeded,
            status_text=status_text,
            token_text=token_text,
            cost_text=cost_text,
            model_name_text=model_name_text,
            rate_limit_text=rate_limit_text,
            price_text=price_text,
        )
        self._progress.start()

    def stop_display(self):
        """Stop the progress display."""
        self._progress.stop()
        table = Table(title="Final Curator Statistics", box=box.ROUNDED)
        table.add_column("Section/Metric", style="cyan")
        table.add_column("Value", style="yellow")

        # Model Information
        table.add_row("Model", "", style="bold magenta")
        table.add_row("Name", f"[blue]{self.model}[/blue]")
        table.add_row("Rate Limit (RPM)", f"[blue]{self.max_requests_per_minute}[/blue]")
        table.add_row("Rate Limit (TPM)", f"[blue]{self.max_tokens_per_minute}[/blue]")

        # Request Statistics
        table.add_row("Requests", "", style="bold magenta")
        table.add_row("Total Processed", str(self.num_tasks_succeeded + self.num_tasks_failed))
        table.add_row("Successful", f"[green]{self.num_tasks_succeeded}[/green]")
        table.add_row("Failed", f"[red]{self.num_tasks_failed}[/red]")

        # Token Statistics
        table.add_row("Tokens", "", style="bold magenta")
        table.add_row("Total Tokens Used", f"{self.total_tokens:,}")
        table.add_row("Total Prompt Tokens", f"{self.total_prompt_tokens:,}")
        table.add_row("Total Completion Tokens", f"{self.total_completion_tokens:,}")
        if self.num_tasks_succeeded > 0:
            table.add_row("Average Tokens per Request", f"{int(self.total_tokens / self.num_tasks_succeeded)}")
            table.add_row("Average Prompt Tokens", f"{int(self.total_prompt_tokens / self.num_tasks_succeeded)}")
            table.add_row("Average Completion Tokens", f"{int(self.total_completion_tokens / self.num_tasks_succeeded)}")
        # Cost Statistics
        table.add_row("Costs", "", style="bold magenta")
        table.add_row("Total Cost", f"[red]${self.total_cost:.4f}[/red]")
        table.add_row("Average Cost per Request", f"[red]${self.total_cost / max(1, self.num_tasks_succeeded):.4f}[/red]")
        table.add_row("Input Cost per 1M Tokens", f"[red]${self.input_cost_per_million:.4f}[/red]")
        table.add_row("Output Cost per 1M Tokens", f"[red]${self.output_cost_per_million:.4f}[/red]")

        # Performance Statistics
        table.add_row("Performance", "", style="bold magenta")
        elapsed_time = time.time() - self.start_time
        elapsed_minutes = elapsed_time / 60
        rpm = self.num_tasks_succeeded / max(0.001, elapsed_minutes)
        table.add_row("Total Time", f"{elapsed_time:.2f}s")
        table.add_row("Average Time per Request", f"{elapsed_time / max(1, self.num_tasks_succeeded):.2f}s")
        table.add_row("Requests per Minute", f"{rpm:.1f}")
        console = Console()
        console.print(table)

    def __str__(self):
        """String representation of the status tracker."""
        return (
            f"Tasks - Started: {self.num_tasks_started}, "
            f"In Progress: {self.num_tasks_in_progress}, "
            f"Succeeded: {self.num_tasks_succeeded}, "
            f"Failed: {self.num_tasks_failed}, "
            f"Already Completed: {self.num_tasks_already_completed}\n"
            f"Errors - API: {self.num_api_errors}, "
            f"Rate Limit: {self.num_rate_limit_errors}, "
            f"Other: {self.num_other_errors}, "
            f"Total: {self.num_other_errors + self.num_api_errors + self.num_rate_limit_errors}"
        )

    def update_capacity(self):
        """Update available capacity based on time elapsed."""
        current_time = time.time()
        seconds_since_update = current_time - self.last_update_time

        self.available_request_capacity = min(
            self.available_request_capacity + self.max_requests_per_minute * seconds_since_update / 60.0,
            self.max_requests_per_minute,
        )

        self.available_token_capacity = min(
            self.available_token_capacity + self.max_tokens_per_minute * seconds_since_update / 60.0,
            self.max_tokens_per_minute,
        )

        self.last_update_time = current_time

    def has_capacity(self, token_estimate: int) -> bool:
        """Check if there's enough capacity for a request."""
        self.update_capacity()
        has_capacity = self.available_request_capacity >= 1 and self.available_token_capacity >= token_estimate
        if not has_capacity:
            logger.debug(
                f"No capacity for request with {token_estimate} tokens. "
                f"Available capacity: {int(self.available_token_capacity)} tokens, "
                f"{int(self.available_request_capacity)} requests."
            )
        return has_capacity

    def consume_capacity(self, token_estimate: int):
        """Consume capacity for a request."""
        self.available_request_capacity -= 1
        self.available_token_capacity -= token_estimate

    def __del__(self):
        """Ensure progress is stopped on deletion."""
        if hasattr(self, "_progress"):
            self._progress.stop()
