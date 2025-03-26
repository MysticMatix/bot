from abc import ABC, abstractmethod
from typing import List, Dict, Any, Callable, Optional


class LLM(ABC):
    """
    Abstract base class for LLM implementations.
    This will be implemented by specific LLM providers like OpenAI, Anthropic, etc.
    """
    
    def __init__(self, config: Dict[str, Any], system_prompt: str, functions: Optional[List[Callable]] = None):
        """
        Initialize the LLM.
        
        Args:
            config: A dictionary containing configuration parameters for the LLM
            system_prompt: The system prompt to use for the conversation
            functions: A list of callable functions that the LLM can use
        """
        self._config = config or {}
        self._system_prompt = system_prompt
        self._functions = functions or []
    
    @abstractmethod
    def query(self, prompt: str, temperature: float = 0.7) -> Dict[str, Any]:
        """
        Send a query to the LLM.
        
        Args:
            prompt: The prompt to send to the LLM
            temperature: The temperature to use for the response (0.0 - 1.0)
                         Lower values make the response more deterministic
                         
        Returns:
            Dict: A dictionary containing the response and any additional metadata
        """
        pass
    
    def clear_history(self) -> None:
        """
        Clear the conversation history.
        """
        pass
    
    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get the conversation history.
        
        Returns:
            List[Dict]: A list of conversation entries
        """
        pass
    
    @property
    def system_prompt(self) -> str:
        """
        Get the system prompt.
        
        Returns:
            str: The system prompt
        """
        return self._system_prompt
    
    @property
    def functions(self) -> List[Callable]:
        """
        Get the list of functions.
        
        Returns:
            List[Callable]: The list of functions
        """
        return self._functions.copy()