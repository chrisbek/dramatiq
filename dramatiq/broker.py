from .errors import ActorNotFound
from .logging import get_logger
from .middleware import AgeLimit, Prometheus, Retries, TimeLimit

#: The global broker instance.
global_broker = None

#: The list of middleware that are enabled by default.
default_middleware = [Prometheus, AgeLimit, TimeLimit, Retries]


def get_broker():
    """Get the global broker instance.  If no global broker is set,
    this initializes a RabbitmqBroker and returns that.

    Returns:
      Broker: The default Broker.
    """
    global global_broker
    if global_broker is None:
        from .brokers.rabbitmq import RabbitmqBroker
        set_broker(RabbitmqBroker())
    return global_broker


def set_broker(broker):
    """Configure the global broker instance.

    Parameters:
      broker(Broker): The broker instance to use by default.
    """
    global global_broker
    global_broker = broker


class Broker:
    """Base class for broker implementations.

    Parameters:
      middleware(list[Middleware]): The set of middleware that apply
        to this broker.  If you supply this parameter, you are
        expected to declare *all* middleware.  Most of the time,
        you'll want to use :meth:`.add_middleware` instead.
    """

    def __init__(self, middleware=None):
        self.logger = get_logger(__name__, type(self))
        self.actors = {}
        self.queues = {}
        self.delay_queues = set()

        self.actor_options = set()
        self.middleware = []

        middleware = middleware or [m() for m in default_middleware]
        for m in middleware:
            self.add_middleware(m)

    def emit_before(self, signal, *args, **kwargs):
        for middleware in self.middleware:
            try:
                getattr(middleware, f"before_{signal}")(self, *args, **kwargs)
            except Exception:
                self.logger.critical("Unexpected failure in before_%s.", signal, exc_info=True)

    def emit_after(self, signal, *args, **kwargs):
        for middleware in reversed(self.middleware):
            try:
                getattr(middleware, f"after_{signal}")(self, *args, **kwargs)
            except Exception:
                self.logger.critical("Unexpected failure in after_%s.", signal, exc_info=True)

    def add_middleware(self, middleware):
        """Add a middleware object to this broker.
        """
        self.middleware.append(middleware)
        self.actor_options |= middleware.actor_options

        for actor_name in self.get_declared_actors():
            middleware.after_declare_actor(self, actor_name)

        for queue_name in self.get_declared_queues():
            middleware.after_declare_queue(self, queue_name)

        for queue_name in self.get_declared_delay_queues():
            middleware.after_declare_delay_queue(self, queue_name)

    def close(self):
        """Close this broker and perform any necessary cleanup actions.
        """

    def consume(self, queue_name, prefetch=1, timeout=30000):  # pragma: no cover
        """Get an iterator that consumes messages off of the queue.

        Raises:
          QueueNotFound: If the given queue was never declared.

        Parameters:
          queue_name(str): The name of the queue to consume messages off of.
          prefetch(int): The number of messages to prefetch per consumer.
          timeout(int): The amount of time in milliseconds to idle for.

        Returns:
          Consumer: A message iterator.
        """
        raise NotImplementedError

    def declare_actor(self, actor):  # pragma: no cover
        """Declare a new actor on this broker.  Declaring an Actor
        twice replaces the first actor with the second by name.

        Parameters:
          actor(Actor): The actor being declared.
        """
        self.emit_before("declare_actor", actor)
        self.declare_queue(actor.queue_name)
        self.actors[actor.actor_name] = actor
        self.emit_after("declare_actor", actor)

    def declare_queue(self, queue_name):  # pragma: no cover
        """Declare a queue on this broker.  This method must be
        idempotent.

        Parameters:
          queue_name(str): The name of the queue being declared.
        """
        raise NotImplementedError

    def enqueue(self, message, *, delay=None):  # pragma: no cover
        """Enqueue a message on this broker.

        Parameters:
          message(Message): The message to enqueue.
          delay(int): The number of milliseconds to delay the message for.
        """
        raise NotImplementedError

    def get_actor(self, actor_name):  # pragma: no cover
        """Look up an actor by its name.

        Raises:
          ActorNotFound: If the actor was never declared.

        Returns:
          Actor: The actor.
        """
        try:
            return self.actors[actor_name]
        except KeyError:
            raise ActorNotFound(actor_name)

    def get_declared_actors(self):  # pragma: no cover
        """Returns a list of all the named actors declared on this broker.
        """
        return self.actors.keys()

    def get_declared_queues(self):  # pragma: no cover
        """Returns a list of all the named queues declared on this broker.
        """
        return self.queues.keys()

    def get_declared_delay_queues(self):  # pragma: no cover
        """Returns the list of all the named delay queues declared on
        this broker.
        """
        return self.delay_queues.copy()


class Consumer:
    """Consumers iterate over messages on a queue.

    Consumers and their MessageProxies are *not* thread-safe.
    """

    def __iter__(self):  # pragma: no cover
        """Returns this instance as a Message iterator.
        """
        return self

    def ack(self, message):  # pragma: no cover
        """Acknowledge the given message.

        Parameters:
          message(MessageProxy)
        """
        raise NotImplementedError

    def nack(self, message):  # pragma: no cover
        """Reject the given message.

        Parameters:
          message(MessageProxy)
        """
        raise NotImplementedError

    def __next__(self):  # pragma: no cover
        """Retrieve the next message off of the queue.  This method
        blocks until a message becomes available.

        Returns:
          MessageProxy: A transparent proxy around a Message that can
          be used to acknowledge or reject it once it's done being
          processed.
        """
        raise NotImplementedError

    def close(self):
        """Close this consumer and perform any necessary cleanup actions.
        """


class MessageProxy:
    """Base class for messages returned by :meth:`Broker.consume`.
    """

    def __init__(self, message):
        self.failed = False
        self._message = message

    def fail(self):
        """Mark this message for rejection.
        """
        self.failed = True

    def __getattr__(self, name):
        return getattr(self._message, name)

    def __str__(self):
        return str(self._message)

    def __lt__(self, other):
        # This can get called if two messages have the same priority
        # in a queue.  If that's the case, we don't care which runs
        # first.
        return True

    def __eq__(self, other):
        if isinstance(other, MessageProxy):
            return self._message == other._message
        return self._message == other
