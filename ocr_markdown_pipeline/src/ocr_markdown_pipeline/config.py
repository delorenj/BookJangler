from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional


class Config(BaseModel):
    """Configuration for the OCR Markdown Pipeline"""
    input_directory: Path = Field(
        default=Path.home() / "pages",
        description="Directory containing images to process"
    )
    output_file: Path = Field(
        default=Path("output.md"),
        description="Path to output markdown file"
    )
    image_extensions: list[str] = Field(
        default=["png", "jpg", "jpeg"],
        description="List of image extensions to process"
    )
    sort_files: bool = Field(
        default=True,
        description="Whether to sort files by name before processing"
    )

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def from_cli(cls, input_dir: Optional[str] = None, output_file: Optional[str] = None) -> "Config":
        """Create config from CLI arguments"""
        kwargs = {}
        if input_dir:
            kwargs["input_directory"] = Path(input_dir)
        if output_file:
            kwargs["output_file"] = Path(output_file)
        return cls(**kwargs)
