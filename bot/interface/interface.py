from abc import ABC, abstractmethod
import threading
from typing import Callable, Any, Dict


class Interface(ABC):
    """
    Abstract base class for messaging interfaces.
    This will be implemented by specific interfaces like MatrixInterface.
    """
    
    def __init__(self, message_callback: Callable[[str, Any], None], config: Dict = None):
        """
        Initialize the interface with a callback function that will be called when messages are received.
        
        Args:
            message_callback: A function that takes a message string and optional context data
                             and processes the received message
            config: A dictionary containing configuration parameters for the interface
        """
        self._message_callback = message_callback
        self._config = config or {}
        self._running = False
        self._thread = None
    
    @abstractmethod
    def send_message(self, message: str, target: Any = None) -> bool:
        """
        Send a message to a specified target.
        
        Args:
            message: The message to send
            target: The target to send the message to (room, user, channel, etc.)
                   Implementation-specific
                   
        Returns:
            bool: True if the message was sent successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def _run_interface(self) -> None:
        """
        Internal method that contains the actual implementation
        of the interface's message receiving loop.
        This should call self._message_callback when a message is received.
        """
        pass
    
    def start(self) -> bool:
        """
        Start the interface's message receiving loop in a background thread.
        
        Returns:
            bool: True if the interface was started successfully, False otherwise
        """
        if self._running:
            return False
            
        self._running = True
        self._thread = threading.Thread(target=self._run_interface, daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> bool:
        """
        Stop the interface's message receiving loop.
        
        Returns:
            bool: True if the interface was stopped successfully, False otherwise
        """
        if not self._running:
            return False
            
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        return True
        
    @property
    def is_running(self) -> bool:
        """
        Check if the interface is currently running.
        
        Returns:
            bool: True if the interface is running, False otherwise
        """
        return self._running
        
    @property
    def config(self) -> Dict:
        """
        Get the configuration dictionary.
        
        Returns:
            Dict: The configuration dictionary
        """
        return self._config