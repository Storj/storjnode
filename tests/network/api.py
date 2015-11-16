import os
import threading
import tempfile
import time
import shutil
import binascii
import random
import unittest
import btctxstore
import storjnode
import logging
from pyp2p.lib import get_wan_ip
from storjnode.network.server import QUERY_TIMEOUT, WALK_TIMEOUT
from crochet import setup
setup()  # start twisted via crochet


_log = logging.getLogger(__name__)


# change timeouts because everything is local
QUERY_TIMEOUT = QUERY_TIMEOUT / 2
WALK_TIMEOUT = WALK_TIMEOUT / 2

SWARM_SIZE = 64  # tested up to 256
MAX_MESSAGES = 2
PORT = 3000
STORAGE_DIR = tempfile.mkdtemp()
WAN_IP = get_wan_ip()


class TestNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        print("TEST: creating swarm")
        cls.btctxstore = btctxstore.BtcTxStore(testnet=False)
        cls.swarm = []
        for i in range(SWARM_SIZE):

            # isolate swarm
            bootstrap_nodes = [("127.0.0.1", PORT + x) for x in range(i)][-20:]

            # create node
            node = storjnode.network.Node(
                cls.btctxstore.create_wallet(), port=(PORT + i), ksize=16,
                bootstrap_nodes=bootstrap_nodes,
                refresh_neighbours_interval=0.0,
                max_messages=MAX_MESSAGES,
                store_config={STORAGE_DIR: None},
                nat_type="preserving",
                node_type="passive",
                wan_ip=WAN_IP,
                disable_data_transfer=1
            )
            cls.swarm.append(node)

            msg = "TEST: created node {0} @ 127.0.0.1:{1}"
            print(msg.format(node.get_hex_id(), node.port))

        # stabalize network overlay
        print("TEST: stabalize network overlay")
        time.sleep(WALK_TIMEOUT)
        for node in cls.swarm:
            node.refresh_neighbours()
        time.sleep(WALK_TIMEOUT)
        for node in cls.swarm:
            node.refresh_neighbours()
        time.sleep(WALK_TIMEOUT)

        # print("TEST: generating swarm graph")
        # import datetime
        # name = "unittest_network_" + str(datetime.datetime.now())
        # storjnode.network.generate_graph(cls.swarm, name)

        print("TEST: created swarm")

    @classmethod
    def tearDownClass(cls):
        print("TEST: stopping swarm")
        for node in cls.swarm:
            node.stop()
        shutil.rmtree(STORAGE_DIR)

    #################################
    # test util and debug functions #
    #################################

    def test_refresh_neighbours_thread(self):
        interval = QUERY_TIMEOUT * 2
        alice_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("240.0.0.0", 1337)],
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            refresh_neighbours_interval=interval,
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        alice_received = threading.Event()
        alice_node.add_message_handler(lambda s, m: alice_received.set())

        bob_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("127.0.0.1", alice_node.port)],
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            refresh_neighbours_interval=interval,
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        bob_received = threading.Event()
        bob_node.add_message_handler(lambda s, m: bob_received.set())
        time.sleep(interval * 2)  # wait until network overlay stable, 2 peers
        try:
            alice_node.direct_message(bob_node.get_id(), "hi bob")
            time.sleep(0.1)  # wait for despatcher
            self.assertTrue(bob_received.isSet())
            bob_node.direct_message(alice_node.get_id(), "hi alice")
            time.sleep(0.1)  # wait for despatcher
            self.assertTrue(alice_received.isSet())
        finally:
            alice_node.stop()
            bob_node.stop()

    def test_has_public_ip(self):  # for coverage
        random_peer = random.choice(self.swarm)
        result = random_peer.sync_has_public_ip()
        self.assertTrue(isinstance(result, bool))

    def test_get_known_peers(self):  # for coverage
        random_peer = random.choice(self.swarm)
        peers = random_peer.get_known_peers()
        self.assertTrue(isinstance(peers, list))
        for peerid in peers:
            self.assertTrue(isinstance(peerid, str))

    ########################
    # test relay messaging #
    ########################

    def _test_relay_message(self, sender, receiver, success_expected):
        testmessage = binascii.hexlify(os.urandom(32))
        receiver_id = receiver.get_id()
        sender.relay_message(receiver_id, testmessage)
        received = []
        receiver.add_message_handler(lambda s, m: received.append(
            {"source": s, "message": m}
        ))
        time.sleep(QUERY_TIMEOUT)  # wait until relayed

        if not success_expected:
            self.assertEqual(len(received), 0)

        else:  # success expected

            # check one message received
            self.assertEqual(len(received), 1)

            # check if correct message received
            source, message = received[0]["source"], received[0]["message"]
            self.assertEqual(testmessage, message)
            self.assertEqual(source, None)

    def test_relay_messaging_success(self):
        sender = self.swarm[0]
        receiver = self.swarm[SWARM_SIZE - 1]
        self._test_relay_message(sender, receiver, True)

    def test_relay_message_self(self):
        sender = self.swarm[0]
        receiver = self.swarm[0]
        self._test_relay_message(sender, receiver, False)

    def test_relay_messaging(self):
        senders = self.swarm[:]
        random.shuffle(senders)
        receivers = self.swarm[:]
        random.shuffle(receivers)
        for sender, receiver in zip(senders, receivers):
            msg = "TEST: sending relay message from {0} to {1}"
            print(msg.format(sender.get_hex_id(), receiver.get_hex_id()))
            self._test_relay_message(sender, receiver, sender is not receiver)

    def test_relay_message_to_void(self):  # for coverage
        random_peer = random.choice(self.swarm)
        void_id = b"void" * 5
        random_peer.relay_message(void_id, "into the void")
        time.sleep(QUERY_TIMEOUT)  # wait until relayed

    def test_max_relay_messages(self):  # for coverage
        random_peer = random.choice(self.swarm)
        void_id = b"void" * 5

        queued = random_peer.relay_message(void_id, "into the void")
        self.assertTrue(queued)
        queued = random_peer.relay_message(void_id, "into the void")
        self.assertTrue(queued)

        # XXX chance of failure if queue is processed during test
        queued = random_peer.relay_message(void_id, "into the void")
        self.assertFalse(queued)  # relay queue full

        time.sleep(QUERY_TIMEOUT)  # wait until relayed

    def test_relay_message_full_duplex(self):
        alice_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("240.0.0.0", 1337)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        alice_received = threading.Event()
        alice_node.add_message_handler(lambda s, m: alice_received.set())
        bob_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("127.0.0.1", alice_node.port)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        bob_received = threading.Event()
        bob_node.add_message_handler(lambda s, m: bob_received.set())
        time.sleep(QUERY_TIMEOUT)  # wait until network overlay stable, 2 peers
        try:
            alice_node.relay_message(bob_node.get_id(), "hi bob")
            time.sleep(QUERY_TIMEOUT)  # wait until relayed
            self.assertTrue(bob_received.isSet())
            bob_node.relay_message(alice_node.get_id(), "hi alice")
            time.sleep(QUERY_TIMEOUT)  # wait until relayed
            self.assertTrue(alice_received.isSet())
        finally:
            alice_node.stop()
            bob_node.stop()

    @unittest.skip("not implemented")
    def test_receive_invavid_hop_limit(self):
        pass  # FIXME test drop message if max hops exceeded or less than 0

    @unittest.skip("not implemented")
    def test_receive_invalid_distance(self):
        pass  # FIXME test do not relay away from dest

    #########################
    # test direct messaging #
    #########################

    def _test_direct_message(self, sender, receiver, success_expected):
        testmessage = binascii.hexlify(os.urandom(32))
        receiver_id = receiver.get_id()
        received = []
        receiver.add_message_handler(lambda s, m: received.append(
            {"source": s, "message": m}
        ))

        sender_address = sender.direct_message(receiver_id, testmessage)
        time.sleep(0.1)  # wait for despatcher

        if not success_expected:
            self.assertTrue(sender_address is None)  # was not received
            self.assertEqual(len(received), 0)

        else:  # success expected

            # check if got message
            self.assertTrue(sender_address is not None)  # was received

            # check returned transport address is valid
            ip, port = sender_address
            self.assertTrue(storjnode.util.valid_ip(ip))
            self.assertTrue(isinstance(port, int))
            self.assertTrue(port >= 0 and port <= 2**16)

            # check one message received
            self.assertEqual(len(received), 1)

            # check if message and sender match
            source, message = received[0]["source"], received[0]["message"]
            self.assertEqual(testmessage, message)
            self.assertEqual(source, sender.get_id())

    def test_direct_messaging_success(self):
        sender = self.swarm[0]
        receiver = self.swarm[SWARM_SIZE - 1]
        self._test_direct_message(sender, receiver, True)

    def test_direct_messaging_failure(self):
        testmessage = binascii.hexlify(os.urandom(32))
        receiver_id = binascii.unhexlify("DEADBEEF" * 5)
        sender = self.swarm[0]
        result = sender.direct_message(receiver_id, testmessage)
        self.assertTrue(result is None)

    def test_direct_message_self(self):
        sender = self.swarm[0]
        receiver = self.swarm[0]
        self._test_direct_message(sender, receiver, False)

    def test_direct_messaging(self):
        senders = self.swarm[:]
        random.shuffle(senders)
        receivers = self.swarm[:]
        random.shuffle(receivers)
        for sender, receiver in zip(senders, receivers):
            msg = "TEST: sending direct message from {0} to {1}"
            print(msg.format(sender.get_hex_id(), receiver.get_hex_id()))
            self._test_direct_message(sender, receiver, sender is not receiver)

    def test_direct_message_to_void(self):  # for coverage
        peer = storjnode.network.Node(
            self.__class__.btctxstore.create_wallet(),
            bootstrap_nodes=[("240.0.0.0", 1337)],  # isolated peer
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        try:
            void_id = b"void" * 5
            result = peer.direct_message(void_id, "into the void")
            time.sleep(0.1)  # wait for despatcher
            self.assertTrue(result is None)
        finally:
            peer.stop()

    def test_direct_message_full_duplex(self):
        alice_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("240.0.0.0", 1337)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        alice_received = threading.Event()
        alice_node.add_message_handler(lambda s, m: alice_received.set())
        bob_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("127.0.0.1", alice_node.port)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        bob_received = threading.Event()
        bob_node.add_message_handler(lambda s, m: bob_received.set())
        time.sleep(QUERY_TIMEOUT)  # wait until network overlay stable, 2 peers
        try:
            alice_node.direct_message(bob_node.get_id(), "hi bob")
            time.sleep(0.1)  # wait for despatcher
            self.assertTrue(bob_received.isSet())
            bob_node.direct_message(alice_node.get_id(), "hi alice")
            time.sleep(0.1)  # wait for despatcher
            self.assertTrue(alice_received.isSet())
        finally:
            alice_node.stop()
            bob_node.stop()

    def test_max_received_messages(self):
        alice_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("240.0.0.0", 1337)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        bob_node = storjnode.network.Node(
            self.__class__.btctxstore.create_key(),
            bootstrap_nodes=[("127.0.0.1", alice_node.port)],
            refresh_neighbours_interval=0.0,
            max_messages=MAX_MESSAGES,
            store_config={STORAGE_DIR: None},
            nat_type="preserving",
            node_type="passive",
            wan_ip=WAN_IP,
            disable_data_transfer=1
        )
        time.sleep(QUERY_TIMEOUT)  # wait until network overlay stable, 2 peers
        try:
            # XXX stop dispatcher
            bob_node._message_dispatcher_thread_stop = True
            bob_node._message_dispatcher_thread.join()

            message_a = binascii.hexlify(os.urandom(32))
            result = alice_node.direct_message(bob_node.get_id(), message_a)
            self.assertTrue(result is not None)

            message_b = binascii.hexlify(os.urandom(32))
            result = alice_node.direct_message(bob_node.get_id(), message_b)
            self.assertTrue(result is not None)

            message_c = binascii.hexlify(os.urandom(32))
            result = alice_node.direct_message(bob_node.get_id(), message_c)
            self.assertEqual(result, None)
        finally:
            alice_node.stop()
            bob_node.stop()

    ###############################
    # test distributed hash table #
    ###############################

    def test_set_get_item(self):
        inserted = dict([
            ("key_{0}".format(i), "value_{0}".format(i)) for i in range(5)
        ])

        # insert mappping randomly into the swarm
        for key, value in inserted.items():
            random_peer = random.choice(self.swarm)
            random_peer[key] = value

        # retrieve values randomly
        for key, inserted_value in inserted.items():
            random_peer = random.choice(self.swarm)
            found_value = random_peer[key]
            self.assertEqual(found_value, inserted_value)

    ######################
    # test data transfer #
    ######################

    # TODO test data transfer


if __name__ == "__main__":
    unittest.main()
