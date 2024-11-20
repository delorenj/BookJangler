import pytest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from ocr_markdown_pipeline import OCRMarkdownPipeline
from ocr_markdown_pipeline import Config


def test_pipeline_state_initialization():
    """State properly initializes with defaults and accepts valid inputs"""
    pipeline = OCRMarkdownPipeline()
    assert pipeline.state.input_directory == ""
    assert pipeline.state.processed_files == []
    assert pipeline.state.output_file == "output.md"


def test_pipeline_state_validation():
    """State enforces type safety and validation"""
    with pytest.raises(ValidationError):
        OCRMarkdownPipeline().kickoff(inputs={"input_directory": 123})


def test_pipeline_handles_multiple_image_extensions():
    """Pipeline processes multiple image extensions correctly"""
    pipeline = OCRMarkdownPipeline()
    mock_files = [Path("test1.png"), Path("test2.jpg"), Path("test3.jpeg")]

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob") as mock_glob:
                # Set up mock to return different files for different extensions
                def glob_side_effect(pattern):
                    ext = pattern[2:]  # Remove *.
                    return [f for f in mock_files if f.suffix[1:] == ext]

                mock_glob.side_effect = glob_side_effect

                with patch.object(
                    pipeline.ocr_tool, "_run", return_value="Test content"
                ):
                    pipeline.kickoff(inputs={"input_directory": "test_dir"})
                    assert len(pipeline.state.processed_files) == 3


def test_pipeline_sorts_files():
    """Pipeline sorts files when configured to do so"""
    pipeline = OCRMarkdownPipeline()
    pipeline.config.sort_files = True
    mock_files = [Path("c.png"), Path("a.png"), Path("b.png")]

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=mock_files):
                with patch.object(
                    pipeline.ocr_tool, "_run", return_value="Test content"
                ):
                    pipeline.kickoff(inputs={"input_directory": "test_dir"})
                    sorted_files = [str(f) for f in sorted(mock_files)]
                    assert pipeline.state.processed_files == sorted_files


def test_pipeline_handles_empty_directory():
    """Pipeline handles empty directory appropriately"""
    pipeline = OCRMarkdownPipeline()
    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=[]):
                result = pipeline.kickoff(inputs={"input_directory": "empty_dir"})
                assert result.startswith("Error: No supported image files found")


def test_pipeline_handles_failed_image_processing():
    """Pipeline continues processing when some images fail"""
    pipeline = OCRMarkdownPipeline()
    mock_files = [Path("test1.png"), Path("test2.png")]

    def mock_ocr(path):
        if "test1" in str(path):
            return "Success content"
        raise Exception("OCR failed")

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=mock_files):
                with patch.object(pipeline.ocr_tool, "_run", side_effect=mock_ocr):
                    result = pipeline.kickoff(inputs={"input_directory": "test_dir"})
                    assert len(pipeline.state.extracted_texts) == 1
                    assert "Success content" in result


def test_pipeline_output_format():
    """Pipeline produces expected markdown format"""
    pipeline = OCRMarkdownPipeline()
    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=[Path("test.png")]):
                with patch.object(
                    pipeline.ocr_tool, "_run", return_value="Test content"
                ):
                    result = pipeline.kickoff(inputs={"input_directory": "test_dir"})
                    assert "# OCR Extracted Content" in result
                    assert "## Page 1" in result
                    assert "Test content" in result


def test_config_from_cli():
    """Config properly initializes from CLI arguments"""
    config = Config.from_cli(input_dir="/custom/input", output_file="custom_output.md")
    assert str(config.input_directory) == "/custom/input"
    assert str(config.output_file) == "custom_output.md"
    assert "png" in config.image_extensions
    assert config.sort_files is True
