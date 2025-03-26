import google.generativeai as genai
from typing import List, Dict, Any, Callable, Optional
import logging
from .llm import LLM


class GeminiLLM(LLM):
    """
    Implementation of the LLM interface for Google's Gemini API.
    """
    
    def __init__(self, config: Dict[str, Any], system_prompt: str, functions: List[Callable] = []):
        """
        Initialize the Gemini LLM.
        
        Args:
            config: A dictionary containing configuration parameters for Gemini API
                   Required keys:
                   - api_key: Your Gemini API key
                   - model: The Gemini model to use (e.g., "gemini-1.5-pro")
            system_prompt: The system prompt to use for the conversation
            functions: A list of callable functions that the LLM can use
        """
        super().__init__(config, system_prompt, functions)
        self.logger = logging.getLogger("gemini-llm")
        
        # Configure the Gemini API
        try:
            genai.configure(api_key=self._config.get("api_key"))
            self.model = genai.GenerativeModel(
                model_name = self._config.get("model", "gemini-1.5-flash-002"),
                tools = functions,
                system_instruction=system_prompt
            )
            self.chat_session = self.model.start_chat()
                
            self.logger.info(f"Initialized Gemini LLM with model: {self._config.get('model', 'gemini-1.5-pro')}")
        except Exception as e:
            self.logger.error(f"Error initializing Gemini LLM: {e}")
            raise
    
    def query(self, prompt: str, temperature: float = 0.7) -> Dict[str, Any]:
        """
        Send a query to the Gemini API.
        
        Args:
            prompt: The prompt to send to the API
            temperature: The temperature to use for generation (0.0 - 1.0)
            
        Returns:
            Dict: A dictionary containing the response and metadata
                 - content: The text response
                 - raw_response: The raw response object from the API
        """
        try:
            # Call the Gemini API
            response = self.chat_session.send_message(
                prompt,
            )
            
            # Extract the response text
            response_text = response.text
            
            return {
                "content": response_text,
                "raw_response": response,
            }
            
        except Exception as e:
            self.logger.error(f"Error querying Gemini API: {e}")
            return {
                "content": f"Error: {str(e)}",
                "error": str(e)
            }
    
    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get the conversation history.
        
        Returns:
            List[Dict]: A list of conversation entries
        """
        return self.chat_session.history
    
    def clear_history(self) -> None:
        """
        Clear the conversation history.
        """
        self.chat_session.history.clear()
        self.logger.info("Cleared conversation history")