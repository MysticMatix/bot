import asyncio
import logging
import os
from typing import Callable, Dict, Any, Optional

from nio import (
    AsyncClient, SyncResponse, RoomMessageText, InviteMemberEvent,
    SyncError, LoginError, LoginResponse, crypto, exceptions,
    MatrixRoom, ClientConfig, MegolmEvent,

    KeyVerificationEvent, KeyVerificationStart, KeyVerificationCancel,
    KeyVerificationAccept, KeyVerificationKey, KeyVerificationMac,
    RoomKeyRequest
)

from .interface import Interface


class MatrixInterface(Interface):
    """
    Implementation of the Interface for Matrix protocol using matrix-nio.
    """
    
    def __init__(self, message_callback: Callable[[str, Any], None], config: Dict = None):
        """
        Initialize the Matrix interface.
        
        Args:
            message_callback: Function to call when a message is received
            config: Configuration dictionary with Matrix settings
                   Required keys:
                   - server.homeserver: Matrix homeserver URL
                   - user.user_id: Matrix user ID
                   - user.password: Matrix password
                   - server.ssl: Whether to use SSL (bool)
                   - application.next_batch_file: Path to store sync token
                   - sync.timeout: Sync timeout in ms
                   - sync.initial_retry_delay: Initial retry delay for sync
                   - sync.max_retries: Maximum number of sync retries
                   - sync.max_retry_delay: Maximum delay between retries
        """
        super().__init__(message_callback, config)
        
        self.logger = logging.getLogger("matrix-bot")
        
        # Initialize the Matrix client
        client_config = ClientConfig(
            store_sync_tokens=True,
            encryption_enabled=True
        )
        self.client = AsyncClient(
            homeserver=self._config["server"]["homeserver"],
            user=self._config["user"]["user_id"],
            ssl=self._config["server"]["ssl"],
            store_path=self._config.get("application", {}).get("store_path", "matrix.db"),
            config=client_config
        )
        
        # Set up event callbacks
        self.client.add_event_callback(self._handle_room_message, RoomMessageText)
        self.client.add_event_callback(self._handle_invite, InviteMemberEvent)

        self.client.add_event_callback(self._handle_megolm_event, MegolmEvent)

        self.client.add_event_callback(self._handle_key_verification_start, KeyVerificationStart)
        self.client.add_event_callback(self._handle_key_verification_cancel, KeyVerificationCancel)
        self.client.add_event_callback(self._handle_key_verification_key, KeyVerificationKey)
        self.client.add_event_callback(self._handle_key_verification_mac, KeyVerificationMac)

        self.client.add_event_callback(self._handle_room_key_request, RoomKeyRequest)
        
        # Initialize other required variables
        self.next_batch_path = self._config["application"]["next_batch_file"]
        self._event_loop = None
        self._sync_task = None

        self.logger.info("Matrix interface initialized")

    async def _handle_megolm_event(self, room, event):
        """
        Handle encrypted Megolm events by trying to decrypt them.
        """
        self.logger.info(f"Received Megolm event in {room.room_id} from {event.sender}")
        
        try:
            # Try to decrypt the event
            decrypted_event = await self.client.decrypt_event(event)
            if decrypted_event:
                self.logger.info(f"Successfully decrypted event from {event.sender}")
                
                # Handle the decrypted event based on its type
                if isinstance(decrypted_event, RoomMessageText):
                    self._handle_room_message(room, decrypted_event)
                else:
                    self.logger.info(f"Decrypted event type: {type(decrypted_event)}")
                    
        except exceptions.OlmUnverifiedDeviceError as e:
            # This happens when receiving a message from an unverified device
            self.logger.warning(f"Received message from unverified device: {e}")
            # Optionally auto-verify the device (less secure but convenient)
            device_id = e.device_id
            sender = e.sender
            await self.client.verify_device(sender, device_id)
            
        except exceptions.MegolmSessionError as e:
            self.logger.error(f"Megolm session error: {e}")
            # Request room keys if missing
            await self._request_room_keys(room.room_id, event.sender)
            
        except Exception as e:
            self.logger.error(f"Failed to decrypt Megolm event: {e}")

    async def _request_room_keys(self, room_id, sender):
        """
        Request room keys from a specific user.
        
        Args:
            room_id: The room ID for which keys are needed
            sender: The user ID to request keys from
        """
        try:
            self.logger.info(f"Requesting room keys for {room_id} from {sender}")
            request_id = await self.client.request_room_key(room_id, sender)
            self.logger.info(f"Room key request sent with ID: {request_id}")
        except Exception as e:
            self.logger.error(f"Failed to request room keys: {e}")

    async def _handle_room_key_request(self, event):
        """
        Handle incoming room key requests by automatically accepting them.
        """
        self.logger.info(f"Received room key request from {event.sender}")
        try:
            await self.client.confirm_key_share(event)
            self.logger.info(f"Shared room keys with {event.sender}")
        except Exception as e:
            self.logger.error(f"Failed to share room keys: {e}")

    async def _handle_key_verification_start(self, event):
        """
        Handle key verification start events.
        """
        self.logger.info(f"Key verification started by {event.sender}")
        # Auto-accept verification requests
        await self.client.accept_key_verification(event.transaction_id)

    async def _handle_key_verification_cancel(self, event):
        """
        Handle key verification cancellation.
        """
        self.logger.info(f"Key verification cancelled by {event.sender}: {event.reason}")

    async def _handle_key_verification_key(self, event):
        """
        Handle key verification key events.
        """
        self.logger.info(f"Key verification key event from {event.sender}")
        # Auto-accept key verification
        await self.client.confirm_key_verification(event.transaction_id)

    async def _handle_key_verification_mac(self, event):
        """
        Handle key verification MAC events.
        """
        self.logger.info(f"Key verification MAC event from {event.sender}")
        # Auto-accept MAC verification
        await self.client.confirm_key_verification(event.transaction_id)
    
    async def _login(self) -> bool:
        """
        Login to the Matrix server.
        
        Returns:
            bool: True if login was successful, False otherwise
        """
        try:
            self.logger.info("Attempting to log in to Matrix server...")
            response = await self.client.login(self._config["user"]["password"])
            
            if isinstance(response, LoginError):
                self.logger.error(f"Login error: {response}")
                return False
                
            self.logger.info(f"Login successful as {self.client.user}")
            return True
            
        except Exception as e:
            self.logger.error(f"Login failed: {e}")
            return False
    
    async def _initial_sync(self) -> bool:
        """
        Perform an initial sync to get the room state.
        
        Returns:
            bool: True if sync was successful, False otherwise
        """
        # Load the sync token if it exists
        try:
            if os.path.exists(self.next_batch_path):
                with open(self.next_batch_path, "r") as next_batch_token:
                    self.client.next_batch = next_batch_token.read()
                    self.logger.info(f"Loaded sync token: {self.client.next_batch}")
        except Exception as e:
            self.logger.info(f"No previous sync token loaded: {e}")
        
        self.logger.info("Starting initial sync...")
        sync_response = None
        
        # Initial sync with retry mechanism
        retry_delay = self._config["sync"]["initial_retry_delay"]
        max_retries = self._config["sync"]["max_retries"]
        
        for attempt in range(max_retries):
            try:
                sync_response = await self.client.sync(
                    timeout=self._config["sync"]["timeout"]
                )
                
                if isinstance(sync_response, SyncError):
                    self.logger.warning(f"Initial sync error (attempt {attempt+1}/{max_retries}): {sync_response}")
                    if attempt < max_retries - 1:
                        self.logger.info(f"Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self._config["sync"]["max_retry_delay"])
                        continue
                    return False
                
                # If sync was successful, break out of retry loop
                self.logger.info("Initial sync successful")
                
                # Save the sync token
                if sync_response and hasattr(sync_response, 'next_batch'):
                    try:
                        with open(self.next_batch_path, "w") as next_batch_token:
                            next_batch_token.write(sync_response.next_batch)
                    except Exception as e:
                        self.logger.error(f"Error saving sync token: {e}")
                
                return True
                
            except Exception as e:
                self.logger.error(f"Error during initial sync (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self._config["sync"]["max_retry_delay"])
                else:
                    self.logger.critical("Max retries reached.")
                    return False
    
    async def _sync_forever(self):
        """
        Main sync loop to continuously receive events from the Matrix server.
        """
        while self._running:
            try:
                sync_response = await self.client.sync(
                    timeout=self._config["sync"]["timeout"],
                    full_state=False
                )
                
                # Check if the response is an error
                if isinstance(sync_response, SyncError):
                    self.logger.error(f"Sync error: {sync_response}")
                    # Wait before retrying
                    await asyncio.sleep(5)
                    continue
                
                # Save the sync token after processing events
                if hasattr(sync_response, 'next_batch'):
                    try:
                        with open(self.next_batch_path, "w") as next_batch_token:
                            next_batch_token.write(sync_response.next_batch)
                    except Exception as e:
                        self.logger.error(f"Error saving sync token: {e}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error during sync loop: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    def _handle_room_message(self, room, event):
        """
        Callback for handling room messages.
        
        Args:
            room: The room object
            event: The room message event
        """
        # Skip our own messages
        if event.sender == self.client.user:
            return
        
        self.logger.info(f"Message in {room.room_id} from {event.sender}: {event.body}")
        self.client.add_event_callback(self._handle_invite, InviteMemberEvent)
        
        # Mark the message as read
        asyncio.run_coroutine_threadsafe(
            self.client.room_read_markers(
                fully_read_event=event.event_id,
                room_id=room.room_id,
                read_event=event.event_id
            ),
            self._event_loop
        )
        
        # Call the message callback with the message text and context
        context = {
            "room_id": room.room_id,
            "sender": event.sender,
            "event_id": event.event_id,
            "event": event
        }
        
        # self._message_callback(event.body, context)
    
    def send_message(self, message: str, target: str = None) -> bool:
        """
        Send a message to a specified room.
        
        Args:
            message: The message to send
            target: The room ID to send the message to
            
        Returns:
            bool: True if the message was scheduled to be sent, False otherwise
        """
        if not self._running:
            self.logger.error("Cannot send message, interface is not running")
            return False
        
        if not target:
            self.logger.error("No target room specified for message")
            return False
        
        # Create a task to send the message
        asyncio.run_coroutine_threadsafe(
            self._send_message_async(message, target),
            self._event_loop
        )
        return True
    
    async def _send_message_async(self, message: str, room_id: str):
        """
        Async method to send a message to a room.
        
        Args:
            message: The message text to send
            room_id: The room ID to send the message to
        """
        try:
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": message
                }
            )
            self.logger.info(f"Sent message to {room_id}")
        except Exception as e:
            self.logger.error(f"Error sending message to {room_id}: {e}")
    
    def _run_interface(self) -> None:
        """
        Implementation of the message receiving loop.
        Sets up the asyncio event loop and runs the Matrix client.
        """
        # Create a new event loop for this thread
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)
        
        try:
            # Login and initial sync
            login_success = self._event_loop.run_until_complete(self._login())
            if not login_success:
                self.logger.error("Failed to login, shutting down interface")
                self._running = False
                return
            
            sync_success = self._event_loop.run_until_complete(self._initial_sync())
            if not sync_success:
                self.logger.error("Failed initial sync, shutting down interface")
                self._running = False
                return
            
            # Start the sync loop
            self._sync_task = self._event_loop.create_task(self._sync_forever())
            
            # Run the event loop
            self._event_loop.run_forever()
            
        except Exception as e:
            self.logger.error(f"Error in Matrix interface thread: {e}", exc_info=True)
        finally:
            # Clean up
            if self._sync_task and not self._sync_task.done():
                self._sync_task.cancel()
                
            # Close the client session
            self._event_loop.run_until_complete(self.client.close())
            self._event_loop.close()
            self._event_loop = None
    
    def stop(self) -> bool:
        """
        Stop the interface's message receiving loop.
        
        Returns:
            bool: True if the interface was stopped successfully, False otherwise
        """
        if not self._running:
            return False
            
        self._running = False
        
        # Stop the event loop
        if self._event_loop:
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
        
        # Join the thread
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            
        return True
    
    def _handle_invite(self, room, event):
        """
        Callback for handling room invites.
        
        Args:
            room: The room object
            event: The invite event
        """
        # Only handle invites for our user
        if event.state_key != self.client.user_id:
            return
            
        self.logger.info(f"Received invite to room {room.room_id} from {event.sender}")
        
        # Join the room automatically
        asyncio.create_task(self._join_room(room.room_id))
    
    async def _join_room(self, room_id):
        """
        Join a room when invited.
        
        Args:
            room_id: The ID of the room to join
        """
        try:
            self.logger.info(f"Joining room {room_id}")
            response = await self.client.join(room_id)
            
            if isinstance(response, SyncError):
                self.logger.error(f"Failed to join room {room_id}: {response}")
                return False
                
            self.logger.info(f"Successfully joined room {room_id}")
            
            # Optional: Send a message to the room to indicate the bot has joined
            await self._send_message_async("Hello! I've joined this room and am ready to assist.", room_id)
            return True
            
        except Exception as e:
            self.logger.error(f"Error joining room {room_id}: {e}")
            return False
