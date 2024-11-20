import os
import pytest
from pathlib import Path
from ocr_markdown_pipeline.tools.ocr_tool import OCRTool

EXPECTED_TEXT = """The Tale of Hot-Clam Provider and the Great Shaft-Quest

One particularly brown-safe morning, Hot-Clam Provider was practicing their usual routine of
tap-dancing on glam-shoot-wheel compressors when Chute-Master burst through the wall riding
asynchronized lamb-stint.

"Hot-Clam! We have an emergency!" shouted Chute-Master while adjusting his bone-mason
credentials. "Someone has stolen all the shaft-quotients from the Temple of Hot-Ages!"

Hot-Clam gasped, dropping their prized collection of man-towels and boot-gap replicas. "Not the
shaft-quotients! How will Farmer Shawn complete his barnyard-atrocity ritual without them?"

Together they embarked on a quest across Bosto-hut territory, stopping only to consult with the
legendary Professor Hot, keeper of the red-clap loader and master of tungsten-meat preparation.
The professor stroked his glorious megatrough thoughtfully.

"To recover the shaft-quotients," Professor Hot declared while polishing his wizard-of-clam-and-
cheese replicator, "you must first defeat Count Shirley of the Sort-Off Coalition. He's been spotted
near the gont-ream negotiations, practicing forbidden clam legislation."

Hot-Clam Provider and Chute-Master infiltrated the negotiations disguised as members of the
Pine Queen's elasticized wheat entrails orchestra. They found Count Shirley attempting to
synchronize his bovox team with a contraband hot-maximizer-hot-maximizer template.

Just as all seemed lost, Belt-Man appeared with his legendary cold bell, performing an
emergency A-stick maneuver that sent the shaft-quotients flying back to their rightful place in
the Temple of Hot-Ages.

"Boh boh boh boh boh," whispered Chute-Master victoriously.

And that's how Hot-Clam Provider saved the day, earning themselves a permanent spot in the
Painter's Clap Hall of Fame, right next to the display of Longchamp hot-treats and the sacred

ham-mouse-rotunda settlement documents.

The end?"""

@pytest.fixture
def ocr_tool():
    return OCRTool()

@pytest.fixture
def test_files_dir():
    return Path(__file__).parent / "samples"

def test_process_image_with_text(ocr_tool, test_files_dir, tmp_path):
    """
    Test that an image with text:
    1. Returns the exact expected text
    2. Creates a txt file with the same text
    """
    # Arrange
    input_path = test_files_dir / "page-with-text.png"
    expected_output_path = tmp_path / "page-with-text.txt"
    
    # Act
    result = ocr_tool.run(
        image_path=str(input_path),
        output_dir=str(tmp_path)
    )
    
    # Assert
    assert result == EXPECTED_TEXT  # Should match exact text
    assert expected_output_path.exists()  # Should create txt file
    assert expected_output_path.read_text().strip() == EXPECTED_TEXT  # File should contain same text

def test_process_image_without_text(ocr_tool, test_files_dir, tmp_path):
    """
    Test that an image without text:
    1. Returns an empty string
    2. Does not create a txt file
    """
    # Arrange
    input_path = test_files_dir / "page-with-no-text.png"
    expected_output_path = tmp_path / "page-with-no-text.txt"
    
    # Act
    result = ocr_tool.run(
        image_path=str(input_path),
        output_dir=str(tmp_path)
    )
    
    # Assert
    assert result == ""  # Should return empty string
    assert not expected_output_path.exists()  # Should not create txt file

def test_process_nonexistent_image(ocr_tool, tmp_path):
    """
    Test that a nonexistent image:
    1. Returns an empty string
    2. Does not create a txt file
    """
    # Arrange
    input_path = tmp_path / "nonexistent.png"
    expected_output_path = tmp_path / "nonexistent.txt"
    
    # Act
    result = ocr_tool.run(
        image_path=str(input_path),
        output_dir=str(tmp_path)
    )
    
    # Assert
    assert result == ""  # Should return empty string
    assert not expected_output_path.exists()  # Should not create txt file

def test_process_invalid_image(ocr_tool, tmp_path):
    """
    Test that an invalid image file:
    1. Returns an empty string
    2. Does not create a txt file
    """
    # Arrange
    # Create an invalid image file
    input_path = tmp_path / "invalid.png"
    input_path.write_text("Not a real image")
    expected_output_path = tmp_path / "invalid.txt"
    
    # Act
    result = ocr_tool.run(
        image_path=str(input_path),
        output_dir=str(tmp_path)
    )
    
    # Assert
    assert result == ""  # Should return empty string
    assert not expected_output_path.exists()  # Should not create txt file
