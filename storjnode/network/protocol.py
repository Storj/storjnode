import heapq
import umsgpack
import hashlib
import operator
try:
    from Queue import Queue, Full  # py2
except ImportError:
    from queue import Queue, Full  # py3
from storjkademlia.protocol import KademliaProtocol
from storjkademlia.routing import RoutingTable
from storjkademlia.routing import TableTraverser
import storjnode


_log = storjnode.log.getLogger(__name__)


def _findNearest(self, node, k=None, exclude=None):
    k = k or self.ksize
    nodes = []
    for neighbor in TableTraverser(self, node):
        if exclude is None or not (neighbor.id == exclude.id or
                                   neighbor.sameHomeAs(exclude)):
            heapq.heappush(nodes, (node.distanceTo(neighbor), neighbor))
        if len(nodes) == k:
            break

    return list(map(operator.itemgetter(1), heapq.nsmallest(k, nodes)))


RoutingTable.findNeighbors = _findNearest  # XXX monkey patch find neighbors


class Protocol(KademliaProtocol):

    def __init__(self, *args, **kwargs):
        self.messages_history_limit = kwargs.pop("messages_history_limit")
        max_messages = kwargs.pop("max_messages")
        self.max_hop_limit = kwargs.pop("max_hop_limit")
        self.messages_relay = Queue(maxsize=max_messages)
        self.messages_received = Queue(maxsize=max_messages)
        self.messages_history = []
        KademliaProtocol.__init__(self, *args, **kwargs)
        self.log = storjnode.log.getLogger("kademlia.protocol")
        if not storjnode.log.NOISY:
            self.log.setLevel(storjnode.log.LEVEL_QUIET)
        self.noisy = storjnode.log.NOISY

    def has_messages(self):
        return not self.messages_received.empty()

    def get_messages(self):
        #print("In protocol get messages")
        ret = storjnode.util.empty_queue(self.messages_received)
        return ret

    def queue_relay_message(self, entry):
        try:
            self.messages_relay.put_nowait(entry)
            return True
        except Full:
            msg = "Relay message queue full, dropping message for %s"
            address = storjnode.util.node_id_to_address(entry["dest"])
            self.log.warning(msg % address)
            return False

    def queue_received_message(self, message):
        try:
            self.messages_received.put_nowait(message)
            return True
        except Full:
            self.log.warning("Received message queue full, dropping message.")
            return False

    def already_received(self, dest_id, message):
        msghash = self.message_hash(dest_id, message)
        return msghash in self.messages_history

    def message_hash(self, dest_id, message):
        return hashlib.sha256(umsgpack.packb([dest_id, message])).digest()

    def add_to_history(self, dest_id, message):
        msghash = self.message_hash(dest_id, message)
        self.messages_history.append(msghash)
        self.cull_history()

    def cull_history(self):
        while len(self.messages_history) > self.messages_history_limit:
            self.messages_history.pop(0)  # remove oldest

    def rpc_relay_message(self, sender, sender_id,
                          dest_id, hop_limit, message):
        # FIXME self.welcomeIfNewNode(Node(sender_id, sender[0], sender[1]))

        logargs = (sender, storjnode.util.node_id_to_address(sender_id),
                   storjnode.util.node_id_to_address(dest_id), hop_limit)
        msg = "Got relay message from {1} at {0} for {2} with limit {3}."
        self.log.debug(msg.format(*logargs))

        # drop if we already received
        if self.already_received(dest_id, message):
            self.log.debug("Dropping relay message, already received.")
            return None
        self.add_to_history(dest_id, message)

        # message is for this node
        if dest_id == self.sourceNode.id:
            queued = self.queue_received_message(message)
            return (sender[0], sender[1]) if queued else None

        # invalid hop limit
        if not (0 < hop_limit <= self.max_hop_limit):
            msg = "Dropping relay message, bad hop limit {0}."
            self.log.debug(msg.format(hop_limit))
            return None

        # add to relay queue
        queued = self.queue_relay_message({
            "dest": dest_id, "message": message, "hop_limit": hop_limit - 1
        })
        return (sender[0], sender[1]) if queued else None

    def callRelayMessage(self, nodeToAsk, destid, hop_limit, message):

        def on_error(result):
            _log.error(repr(result))

        address = (nodeToAsk.ip, nodeToAsk.port)
        self.log.debug("Sending relay message to {0}:{1}".format(*address))
        deferred = self.relay_message(address, self.sourceNode.id, destid,
                                      hop_limit, message)
        return deferred.addCallback(self.handleCallResponse, nodeToAsk)
