from nio import (AsyncClient, SyncResponse, RoomMessageText, SyncError, LoginError)
import asyncio
import signal
import sys
import time
import json
import toml
import os
import logging

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
config = load_config()

# Initialize client from config
async_client = AsyncClient(
    homeserver=config["server"]["homeserver"],
    user=config["user"]["user_id"],
    ssl=config["server"]["ssl"],
)

# Flag to track if shutdown was requested
shutdown_requested = False
# Track the timeout for force exit
force_exit_timer = None

async def main():
    global force_exit_timer, shutdown_requested
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: request_shutdown())
    
    try:
        # Login with proper error handling
        try:
            logger.info("Attempting to log in to Matrix server...")
            response = await async_client.login(config["user"]["password"])
            if isinstance(response, LoginError):
                logger.error(f"Login error: {response}")
                return
            logger.info(f"Login successful")
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return

        logger.info(f"Logged in as user: {async_client.user}")

        # Load the sync token if it exists
        next_batch_path = config["application"]["next_batch_file"]
        try:
            with open(next_batch_path, "r") as next_batch_token:
                async_client.next_batch = next_batch_token.read()
                logger.info(f"Loaded sync token: {async_client.next_batch}")
        except FileNotFoundError:
            logger.info("No previous sync token found")

        logger.info("Starting initial sync...")
        sync_response = None
        
        # Initial sync with retry mechanism
        retry_delay = config["sync"]["initial_retry_delay"]
        max_retries = config["sync"]["max_retries"]
        for attempt in range(max_retries):
            try:
                sync_response = await async_client.sync(timeout=config["sync"]["timeout"])
                if isinstance(sync_response, SyncError):
                    logger.warning(f"Initial sync error (attempt {attempt+1}/{max_retries}): {sync_response}")
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, config["sync"]["max_retry_delay"])
                        continue
                    return
                # If sync was successful, break out of retry loop
                logger.info("Initial sync successful")
                break
            except Exception as e:
                logger.error(f"Error during initial sync (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, config["sync"]["max_retry_delay"])
                else:
                    logger.critical("Max retries reached. Exiting.")
                    return

        # Main loop - keep syncing and processing messages
        while not shutdown_requested:
            try:
                # Process any messages/events from the sync response
                if sync_response and hasattr(sync_response, 'rooms') and hasattr(sync_response.rooms, 'join'):
                    # Process joined rooms
                    for room_id in sync_response.rooms.join:
                        room = sync_response.rooms.join[room_id]
                        
                        # Process timeline events
                        for event in room.timeline.events:
                            if isinstance(event, RoomMessageText):
                                # Skip our own messages
                                if event.sender == async_client.user:
                                    logger.debug(f"Ignored own message: {event.body}")
                                    continue
                                
                                logger.info(f"Message in {room_id} from {event.sender}: {event.body}")
                                await async_client.room_read_markers(
                                    fully_read_event=event.event_id,
                                    room_id=room_id,
                                    read_event=event.event_id
                                )
                                
                                # Respond to commands
                                if event.body == "!ping":
                                    await async_client.room_send(
                                        room_id=room_id,
                                        message_type="m.room.message",
                                        content={"msgtype": "m.text", "body": "Pong!"}
                                    )
                                    logger.info(f"Sent 'Pong!' response to {room_id}")

                
                # Save the sync token after processing events
                if sync_response and hasattr(sync_response, 'next_batch'):
                    try:
                        with open(next_batch_path, "w") as next_batch_token:
                            next_batch_token.write(sync_response.next_batch)
                    except Exception as e:
                        logger.error(f"Error saving sync token: {e}")
                
                # Perform the next sync
                logger.debug("Syncing again...")  # Lower level for regular operation
                sync_response = await async_client.sync(
                    timeout=config["sync"]["timeout"],
                    full_state=False
                )
                
                # Check if the response is an error
                if isinstance(sync_response, SyncError):
                    logger.error(f"Sync error: {sync_response}")
                    # Wait before retrying
                    await asyncio.sleep(5)
                    continue
                
            except asyncio.CancelledError:
                # Handle cancellation (e.g., during shutdown)
                break
            except Exception as e:
                logger.error(f"Error during sync loop: {e}", exc_info=True)
                # Wait before retrying
                await asyncio.sleep(5)
        
        logger.info("Main loop exited")
    
    finally:
        # Make sure we properly close the client session when exiting
        logger.info("Closing the client session...")
        await async_client.close()
        
        # Save the sync token in case of a clean shutdown
        if sync_response and hasattr(sync_response, 'next_batch'):
            try:
                with open(next_batch_path, "w") as next_batch_token:
                    next_batch_token.write(sync_response.next_batch)
                    logger.info("Final sync token saved")
            except Exception as e:
                logger.error(f"Error saving final sync token: {e}")

def request_shutdown():
    global shutdown_requested, force_exit_timer
    shutdown_requested = True
    logger.info("Shutdown requested, finishing current operations...")
    
    # Set a timer to force exit if graceful shutdown takes too long
    loop = asyncio.get_event_loop()
    force_exit_timer = loop.call_later(10, force_exit)

def force_exit():
    logger.warning("Force exiting after timeout...")
    sys.exit(1)

if __name__ == "__main__":
    try:
        logger.info("Matrix bot starting up...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)