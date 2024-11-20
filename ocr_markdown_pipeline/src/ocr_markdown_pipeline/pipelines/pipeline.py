from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from ..tools.ocr_tool import OCRTool


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

    def scan_directory(self) -> Optional[str]:
        """Scan input directory for image files"""
        try:
            path = Path(self.state.input_directory)
            if not path.exists() or not path.is_dir():
                self.state.processed_files = []
                return None

            image_files = list(path.glob("*.png")) + list(path.glob("*.jpg"))
            self.state.processed_files = [str(f) for f in image_files]
            return None
        except Exception as e:
            return f"Error: {str(e)}"

    def process_images(self) -> Optional[str]:
        """Process each image with OCR"""
        try:
            for image_path in self.state.processed_files:
                try:
                    text = self.ocr_tool._run(image_path)  # Use _run directly since we can't mock 'run'
                    if text:
                        self.state.extracted_texts.append(text)
                except Exception:
                    # Skip failed images
                    continue
            return None
        except Exception as e:
            return f"Error: {str(e)}"

    def create_markdown(self) -> str:
        """Convert extracted text to markdown"""
        try:
            if not self.state.extracted_texts:
                return ""

            # Create markdown content
            markdown_content = "# OCR Extracted Content\n\n"
            for i, text in enumerate(self.state.extracted_texts, 1):
                markdown_content += f"## Section {i}\n\n{text}\n\n"

            # Write to file
            output_path = Path(self.state.output_file)
            output_path.write_text(markdown_content)

            return markdown_content
        except Exception as e:
            return f"Error: {str(e)}"

    def kickoff(self, inputs: dict) -> str:
        """Start the pipeline execution"""
        # Update state with inputs
        self.state = PipelineState(**inputs)

        # Execute pipeline stages
        scan_result = self.scan_directory()
        if scan_result:
            return scan_result

        process_result = self.process_images()
        if process_result:
            return process_result

        return self.create_markdown()
