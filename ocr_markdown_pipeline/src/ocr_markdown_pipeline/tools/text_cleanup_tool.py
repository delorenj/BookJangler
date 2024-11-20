# tools/text_cleanup_tool.py
from crewai.tools import BaseTool
import openai
from pydantic import BaseModel, Field


class TextCleanupInput(BaseModel):
    text: str = Field(..., description="OCR text to clean up")
    context: str = Field(..., description="Context about the source material")


class TextCleanupTool(BaseTool):
    name: str = "Text Cleanup Tool"
    description: str = "Clean up OCR-extracted text using language model analysis"
    args_schema: type[BaseModel] = TextCleanupInput

    def _run(self, text: str, context: str) -> str:
        # Initialize your LLM here
        prompt = f"""
        Clean up this OCR-extracted text, fixing obvious errors and missing words.
        Use context clues and best judgment to reconstruct the original text.
        Maintain the original meaning and style.
        
        Context: {context}
        Text: {text}
        """
        # Call LLM and return cleaned text
        # For example using OpenAI:
        response = openai.ChatCompletion.create(
            model="gpt-4", messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
