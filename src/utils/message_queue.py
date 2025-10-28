"""Multi-threaded message queue manager for BetterForward."""

import queue
import threading
from collections import defaultdict, deque
from typing import Callable

from telebot.types import Message
from telebot.util import antiflood

from src import config
from src.config import logger, _


class MessageQueueManager:
    """
    Manages message processing with multiple worker threads.
    
    Ensures:
    1. Messages from the same user are processed sequentially
    2. Thread-safe SQLite operations
    3. Efficient parallel processing for different users
    """

    def __init__(self, handler_func: Callable, num_workers: int = 5):
        """
        Initialize the message queue manager.
        
        Args:
            handler_func: The function to call for processing messages
            num_workers: Number of worker threads (default: 5)
        """
        self.handler_func = handler_func
        self.num_workers = num_workers

        # Main queue for incoming messages
        self.main_queue = queue.Queue()

        # Per-user message queues
        self.user_queues = defaultdict(deque)

        # Set of user_ids currently being processed
        self.processing_users = set()

        # Lock for thread-safe operations on user_queues and processing_users
        self.lock = threading.Lock()

        # Worker threads
        self.workers = []

        # Start workers
        self._start_workers()

    def _start_workers(self):
        """Start worker threads."""
        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self._worker,
                name=f"MessageWorker-{i + 1}",
                daemon=True
            )
            worker.start()
            self.workers.append(worker)
        logger.info(_("Started {} message processing workers").format(self.num_workers))

    def _get_user_id(self, message: Message) -> int | str:
        """Extract user_id from message for queue grouping."""
        if message.chat.type == 'private':
            return message.from_user.id
        else:
            # For group messages, use thread_id as identifier
            # This ensures messages in the same thread are processed sequentially
            return f"thread_{message.message_thread_id}"

    def _worker(self):
        """Worker thread that processes messages."""
        while not config.stop:
            try:
                # Get message from main queue with timeout
                message = self.main_queue.get(timeout=1)

                # Get user identifier
                user_id = self._get_user_id(message)

                # Check if this user is already being processed
                with self.lock:
                    if user_id in self.processing_users:
                        # User is being processed, add to user's queue
                        self.user_queues[user_id].append(message)
                        self.main_queue.task_done()
                        continue
                    else:
                        # Mark user as being processed
                        self.processing_users.add(user_id)

                # Process this message and any queued messages for this user
                self._process_user_messages(user_id, message)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(_("Worker error: {}").format(e))
                from traceback import print_exc
                print_exc()

    def _process_user_messages(self, user_id, first_message: Message):
        """
        Process all messages for a specific user sequentially.
        
        Args:
            user_id: The user identifier
            first_message: The first message to process
        """
        try:
            # Process the first message
            antiflood(self.handler_func, first_message)

            # Process any queued messages for this user
            while True:
                with self.lock:
                    if not self.user_queues[user_id]:
                        # No more messages for this user
                        self.processing_users.discard(user_id)
                        # Clean up empty queue
                        if user_id in self.user_queues:
                            del self.user_queues[user_id]
                        break
                    # Get next message for this user
                    next_message = self.user_queues[user_id].popleft()

                # Process the message
                try:
                    antiflood(self.handler_func, next_message)
                except Exception as e:
                    logger.error(_("Failed to process message for user {}: {}").format(user_id, e))
                    from traceback import print_exc
                    print_exc()

        except Exception as e:
            logger.error(_("Failed to process message for user {}: {}").format(user_id, e))
            from traceback import print_exc
            print_exc()

            # Make sure to remove user from processing set
            with self.lock:
                self.processing_users.discard(user_id)
        finally:
            # Mark main queue task as done
            self.main_queue.task_done()

    def put(self, message: Message):
        """
        Add a message to the processing queue.
        
        Args:
            message: The message to process
        """
        self.main_queue.put(message)

    def stop(self):
        """Stop all workers and wait for them to finish."""
        logger.info(_("Stopping message queue manager..."))

        # Wait for queue to be empty
        self.main_queue.join()

        # Workers will stop when 'stop' flag is set globally
        for worker in self.workers:
            worker.join(timeout=5)

        logger.info(_("Message queue manager stopped"))

    def get_stats(self) -> dict:
        """Get current queue statistics."""
        with self.lock:
            return {
                "main_queue_size": self.main_queue.qsize(),
                "processing_users_count": len(self.processing_users),
                "user_queues_count": len(self.user_queues),
                "total_queued_messages": sum(len(q) for q in self.user_queues.values()),
                "workers_count": len(self.workers)
            }
