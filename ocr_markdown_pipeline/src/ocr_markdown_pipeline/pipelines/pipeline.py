from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console
from ..tools.ocr_tool import OCRTool
from ..config import Config


class PipelineState(BaseModel):
    """State management for the OCR to Markdown pipeline"""
    input_directory: str = ""
    processed_files: List[str] = []
    extracted_texts: List[str] = []
    output_file: str = "output.md"


class OCRMarkdownPipeline:
    """Pipeline for converting image-based text to markdown"""

    def __init__(self):
        self.state = PipelineState()
        self.ocr_tool = OCRTool()
        self.config = Config()
        self.console = Console()

    def scan_directory(self) -> Optional[str]:
        """Scan input directory for image files"""
        try:
            with self.console.status("[bold green]Scanning directory...") as status:
                path = Path(self.state.input_directory)
                if not path.exists() or not path.is_dir():
                    self.state.processed_files = []
                    return f"Error: Directory {path} does not exist or is not a directory"

                # Collect all files with supported extensions
                image_files = []
                for ext in self.config.image_extensions:
                    status.update(f"[bold green]Looking for .{ext} files...")
                    image_files.extend(path.glob(f"*.{ext}"))

                if self.config.sort_files:
                    status.update("[bold green]Sorting files...")
                    image_files.sort()

                self.state.processed_files = [str(f) for f in image_files]

                if not self.state.processed_files:
                    return f"Error: No supported image files found in {path}"

                return None
        except Exception as e:
            return f"Error scanning directory: {str(e)}"

    def process_images(self) -> Optional[str]:
        """Process each image with OCR"""
        try:
            successful_extractions = 0
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
            ) as progress:
                task = progress.add_task(
                    "[bold green]Processing images...",
                    total=len(self.state.processed_files)
                )
                
                for image_path in self.state.processed_files:
                    progress.update(task, description=f"[bold green]Processing {Path(image_path).name}")
                    try:
                        text = self.ocr_tool._run(image_path)
                        if text and text.strip():
                            self.state.extracted_texts.append(text.strip())
                            successful_extractions += 1
                    except Exception as e:
                        self.console.print(f"[yellow]Warning: Failed to process {image_path}: {str(e)}")
                    finally:
                        progress.advance(task)

            if successful_extractions == 0:
                return "Error: No text could be extracted from any images"

            return None
        except Exception as e:
            return f"Error processing images: {str(e)}"

    def create_markdown(self) -> str:
        """Convert extracted text to markdown"""
        try:
            if not self.state.extracted_texts:
                return ""

            with self.console.status("[bold green]Creating markdown document...") as status:
                # Create markdown content
                markdown_content = "# OCR Extracted Content\n\n"
                for i, text in enumerate(self.state.extracted_texts, 1):
                    status.update(f"[bold green]Adding content from page {i}...")
                    markdown_content += f"## Page {i}\n\n{text}\n\n"

                # Write to file
                status.update("[bold green]Writing to file...")
                output_path = Path(self.state.output_file)
                output_path.write_text(markdown_content)

                return markdown_content
        except Exception as e:
            return f"Error creating markdown: {str(e)}"

    def kickoff(self, inputs: dict) -> str:
        """Start the pipeline execution"""
        self.console.print("\n[bold blue]Starting OCR Markdown Pipeline[/bold blue]\n")
        
        # Update state with inputs
        self.state = PipelineState(**inputs)

        # Execute pipeline stages
        scan_result = self.scan_directory()
        if scan_result:
            self.console.print(f"\n[bold red]{scan_result}[/bold red]\n")
            return scan_result

        process_result = self.process_images()
        if process_result:
            self.console.print(f"\n[bold red]{process_result}[/bold red]\n")
            return process_result

        result = self.create_markdown()
        if result.startswith("Error"):
            self.console.print(f"\n[bold red]{result}[/bold red]\n")
        else:
            self.console.print(f"\n[bold green]Successfully created {self.state.output_file}[/bold green]\n")
        
        return result
