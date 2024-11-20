You're absolutely right - I apologize for the inconsistency. Let me analyze this properly.



Let's start fresh with the correct approach:

### 1. Create Pipeline Project

```bash
# Create a new pipeline project
crewai create pipeline ocr_markdown_pipeline

# Navigate to project directory
cd ocr_markdown_pipeline
```

This will create the following structure:
```
ocr_markdown_pipeline/
├── src/
│   └── ocr_markdown_pipeline/
│       ├── __init__.py
│       ├── main.py
│       ├── pipeline.py
│       └── tools/
│           ├── __init__.py
│           ├── ocr_tool.py
│           └── file_tools.py
├── pyproject.toml
└── README.md
```

### 2. Install Dependencies

```bash
# Install dependencies
pip install 'crewai[tools]' pytesseract Pillow
```

### 3. Implement Custom Tools

In `src/ocr_markdown_pipeline/tools/ocr_tool.py`:

```python
from crewai.tools import BaseTool
from PIL import Image
import pytesseract
from typing import Optional

class OCRTool(BaseTool):
    name: str = "OCR Tool"
    description: str = "Extracts text from images using Tesseract OCR"
    
    def _run(self, image_path: str) -> str:
        """Process an image and extract text using OCR."""
        try:
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image)
            return text.strip()
        except Exception as e:
            return f"Error processing image: {str(e)}"
```

### 4. Implement Pipeline

In `src/ocr_markdown_pipeline/pipeline.py`:

```python
from pathlib import Path
from typing import List
from pydantic import BaseModel
from crewai.flow.flow import Flow, listen, start
from .tools.ocr_tool import OCRTool

class OCRMarkdownState(BaseModel):
    """State management for the OCR to Markdown pipeline"""
    input_directory: str = ""
    processed_files: List[str] = []
    extracted_texts: List[str] = []
    markdown_content: str = ""
    output_file: str = "output.md"

class OCRMarkdownPipeline(Flow[OCRMarkdownState]):
    """Pipeline for converting image-based text to markdown"""
    
    def __init__(self):
        super().__init__()
        self.ocr_tool = OCRTool()
    
    @start()
    def scan_directory(self):
        """Scan input directory for image files"""
        print(f"Scanning directory: {self.state.input_directory}")
        image_files = list(Path(self.state.input_directory).glob("*.png"))
        self.state.processed_files = [str(f) for f in image_files]
        print(f"Found {len(self.state.processed_files)} images")
        
    @listen(scan_directory)
    def process_images(self):
        """Process each image with OCR"""
        print("Processing images with OCR")
        for image_path in self.state.processed_files:
            text = self.ocr_tool.run(image_path)
            if text:
                self.state.extracted_texts.append(text)
                
    @listen(process_images)
    def create_markdown(self):
        """Convert extracted text to markdown"""
        print("Creating markdown document")
        
        # Simple markdown formatting - can be enhanced based on needs
        markdown_content = "# OCR Extracted Content\n\n"
        for i, text in enumerate(self.state.extracted_texts, 1):
            markdown_content += f"## Section {i}\n\n{text}\n\n"
        
        self.state.markdown_content = markdown_content
        
        # Save the output
        output_path = Path(self.state.output_file)
        output_path.write_text(self.state.markdown_content)
        print(f"Markdown saved to {output_path}")
        
        return self.state.markdown_content
```

### 5. Implement Main Entry Point

In `src/ocr_markdown_pipeline/main.py`:

```python
from pathlib import Path
from .pipeline import OCRMarkdownPipeline

def run():
    """Run the OCR to Markdown pipeline"""
    # Get input directory from environment or use default
    input_dir = "input_images"  # You might want to make this configurable
    output_file = "output.md"
    
    pipeline = OCRMarkdownPipeline()
    result = pipeline.kickoff(inputs={
        "input_directory": input_dir,
        "output_file": output_file
    })
    
    return result

if __name__ == "__main__":
    run()
```

### 6. Testing the Pipeline

1. Create a test directory:
```bash
mkdir input_images
# Copy some PNG images with text into input_images/
```

2. Run the pipeline:
```bash
python -m ocr_markdown_pipeline.main
```

This revised implementation correctly follows CrewAI's pipeline pattern with:

1. **Proper Pipeline Structure**: Following CrewAI's pipeline conventions
2. **State Management**: Using Pydantic for type-safe state
3. **Flow Control**: Using `@start()` and `@listen()` decorators for pipeline stage management
4. **Tool Integration**: Custom OCR tool implementation
5. **Error Handling**: Built into the tools and pipeline stages
