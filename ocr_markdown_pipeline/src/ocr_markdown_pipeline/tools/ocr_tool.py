from crewai.tools import BaseTool
from PIL import Image
import pytesseract
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class OCRToolInput(BaseModel):
    """Input schema for OCR tool"""
    image_path: str = Field(..., description="Path to the image file to process")
    output_dir: Optional[str] = Field(None, description="Optional directory to save txt output file")


class OCRTool(BaseTool):
    name: str = "OCR Tool"
    description: str = "Extracts text from images using Tesseract OCR"
    args_schema: type[BaseModel] = OCRToolInput

    def _run(self, image_path: str, output_dir: Optional[str] = None) -> str:
        """
        Process an image and extract text using OCR.
        
        Args:
            image_path: Path to the image file to process
            output_dir: Optional directory to save txt output file. If not provided,
                       no file will be created.
        
        Returns:
            str: Extracted text if found, empty string if no text or error
        """
        try:
            # Validate image exists
            if not Path(image_path).exists():
                return ""

            # Try to open and process image
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image)
            text = text.strip()

            # Only create output file if we found text and have an output directory
            if text and output_dir:
                output_path = Path(output_dir) / Path(image_path).stem
                output_path = output_path.with_suffix('.txt')
                output_path.write_text(text)

            return text

        except (OSError, IOError, pytesseract.TesseractError):
            # Handle any file, image processing, or OCR errors
            return ""

    async def _arun(self, *args, **kwargs) -> str:
        """Async implementation - just calls sync version"""
        return self._run(*args, **kwargs)
