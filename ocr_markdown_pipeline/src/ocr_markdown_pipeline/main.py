#!/usr/bin/env python
import argparse
import sys
from pathlib import Path
from rich.console import Console
from .pipelines.pipeline import OCRMarkdownPipeline
from .config import Config


def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Convert images containing text to a markdown document"
    )
    parser.add_argument(
        "-i", "--input-dir",
        help="Directory containing images to process (default: ~/pages)",
        type=str
    )
    parser.add_argument(
        "-o", "--output-file",
        help="Path to output markdown file (default: output.md)",
        type=str
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point"""
    console = Console()
    
    try:
        # Parse arguments and create config
        args = parse_args()
        config = Config.from_cli(
            input_dir=args.input_dir,
            output_file=args.output_file
        )

        # Show initial configuration
        console.print("\n[bold cyan]Configuration:[/bold cyan]")
        console.print(f"[cyan]Input Directory:[/cyan] {config.input_directory}")
        console.print(f"[cyan]Output File:[/cyan] {config.output_file}")
        console.print(f"[cyan]Supported Extensions:[/cyan] {', '.join(config.image_extensions)}")
        console.print()

        # Initialize and run pipeline
        pipeline = OCRMarkdownPipeline()
        result = pipeline.kickoff({
            "input_directory": str(config.input_directory),
            "output_file": str(config.output_file)
        })

        # Check result
        if result.startswith("Error:"):
            console.print(f"\n[bold red]Pipeline failed:[/bold red] {result}", file=sys.stderr)
            return 1
        
        return 0

    except Exception as e:
        console.print(f"\n[bold red]An error occurred:[/bold red] {str(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
