import pytest
from pathlib import Path
from unittest.mock import patch, Mock
from pydantic import ValidationError
from ocr_markdown_pipeline import OCRMarkdownPipeline


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


def test_pipeline_execution_order():
    """Pipeline stages execute in correct order"""
    pipeline = OCRMarkdownPipeline()
    mock_scan = Mock(return_value=None)
    mock_process = Mock(return_value=None)

    with patch.object(pipeline, "scan_directory", mock_scan):
        with patch.object(pipeline, "process_images", mock_process):
            pipeline.kickoff(inputs={"input_directory": "test_dir"})
            mock_scan.assert_called_once()
            mock_process.assert_called_once()


def test_pipeline_invalid_directory():
    """Pipeline handles invalid directory gracefully"""
    pipeline = OCRMarkdownPipeline()
    with patch.object(Path, "exists", return_value=False):
        result = pipeline.kickoff(inputs={"input_directory": "nonexistent_dir"})
        assert result == ""


def test_pipeline_empty_directory():
    """Pipeline handles empty directory appropriately"""
    pipeline = OCRMarkdownPipeline()
    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=[]):
                result = pipeline.kickoff(inputs={"input_directory": "empty_dir"})
                assert result == ""


def test_pipeline_output_creation():
    """Pipeline produces expected markdown format"""
    pipeline = OCRMarkdownPipeline()
    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "is_dir", return_value=True):
            with patch.object(Path, "glob", return_value=[Path("test.png")]):
                with patch.object(pipeline.ocr_tool, "_run", return_value="Test content"):
                    result = pipeline.kickoff(inputs={"input_directory": "test_images"})
                    assert result.startswith("# OCR Extracted Content")
                    assert Path("output.md").exists()


def test_pipeline_resource_cleanup():
    """Pipeline properly cleans up resources after execution"""
    pipeline = OCRMarkdownPipeline()
    pipeline.kickoff(inputs={"input_directory": "test_dir"})
    assert pipeline.state.extracted_texts == []
