import signal
import sys
import time
import toml
import os
import logging
import threading

# Import our interface implementation
from interface import MatrixInterface

config_path = "config.toml"

# Configure logging for systemd integration
def setup_logging():
    logger = logging.getLogger("matrix-bot")
    logger.setLevel(logging.INFO)
    
    # Create console handler that logs to stdout/stderr (captured by systemd)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Create a formatter that includes timestamp and log level
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    console_handler.setFormatter(formatter)
    
    # Add the handler to the logger
    logger.addHandler(console_handler)
    
    return logger

# Initialize logger
logger = setup_logging()

# Load configuration
def load_config():
    try:
        with open(config_path, "r") as f:
            return toml.load(f)
    except Exception as e:
        logger.critical(f"Error loading config: {e}")
        sys.exit(1)

# Global config
gconfig = load_config()

# Flag to track if shutdown was requested
shutdown_requested = False

# Track the timeout for force exit
force_exit_timer = None

# Matrix interface
matrix_interface = None

# Message handler callback
def handle_message(message, context):
    """
    Handle incoming messages from Matrix
    
    Args:
        message: The message text
        context: Context data including room_id, sender, etc.
    """
    logger.info(f"Message in {context['room_id']} from {context['sender']}: {message}")
    
    # Respond to commands
    if message == "!ping":
        matrix_interface.send_message("Pong!", context['room_id'])
        logger.info(f"Sent 'Pong!' response to {context['room_id']}")

def request_shutdown():
    global shutdown_requested, force_exit_timer, matrix_interface
    shutdown_requested = True
    logger.info("Shutdown requested, finishing current operations...")
    
    # Stop the Matrix interface
    if matrix_interface:
        if matrix_interface.stop():
            logger.info("Matrix interface stopped")
        else:
            logger.warning("Matrix interface was not running or failed to stop")
    
    # Set a timer to force exit if graceful shutdown takes too long
    force_exit_timer = threading.Timer(10, force_exit)
    force_exit_timer.start()

def force_exit():
    logger.warning("Force exiting after timeout...")
    sys.exit(1)

def main():
    global matrix_interface
    
    logger.info("Matrix bot starting up...")
    
    # Setup signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: request_shutdown())
    
    try:
        # Initialize the Matrix interface with our message handler
        matrix_interface = MatrixInterface(handle_message, gconfig["matrix"])
        
        # Start the interface (which will run in a background thread)
        if matrix_interface.start():
            logger.info("Matrix interface started successfully")
        else:
            logger.error("Failed to start Matrix interface")
            return 1
        
        # Main loop - keep the main thread alive until shutdown is requested
        while not shutdown_requested:
            time.sleep(1)
        
        logger.info("Main loop exited")
        
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)