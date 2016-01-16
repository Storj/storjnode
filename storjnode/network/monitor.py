import time
import bisect
import json
import copy
import storjnode
from collections import OrderedDict
from io import BytesIO
from threading import Thread, RLock
from storjnode.common import THREAD_SLEEP
from storjnode.util import node_id_to_address
from storjnode.network.messages.peers import read as read_peers
from storjnode.network.messages.peers import request as request_peers
from storjnode.network.messages.info import read as read_info
from storjnode.network.messages.info import request as request_info


_log = storjnode.log.getLogger(__name__)


SKIP_BANDWIDTH_TEST = True


DEFAULT_DATA = {
    "peers": None,      # [nodeid, ...]
    "storage": None,    # {"total": int, "used": int, "free": int}
    "network": None,    # {
                        #     "transport": [ip, port],
                        #     "unl": str "is_public": bool
                        # }
    "version": None,    # {"protocol: str, "storjnode": str}
    "platform": None,   # {
                        #   "system": str, "release": str,
                        #   "version": str, "machine": str
                        # }
    "btcaddress": None,
    "bandwidth": None,  # {"send": int, "receive": int}
    "latency": {"info": None, "peers": None, "direct": None},  # TODO direct
    "request": {"tries": 0, "last": 0},
}


class Crawler(object):  # will not scale but good for now

    def __init__(self, node, limit=20, timeout=600):

        # CRAWLER PIPELINE
        self.pipeline_mutex = RLock()

        # sent info and peer requests but not yet received a response
        self.pipeline_scanning = {}  # {node_id: data}
        # |
        # | received info and peer responses and ready for bandwidth test
        # V user ordered dict to have a fifo for bandwidth test
        self.pipeline_scanned = OrderedDict()  # {node_id: data}
        # |
        # V only test bandwidth of one node at a time to ensure best results
        self.pipeline_bandwidth_test = None  # (node_id, data)
        # |
        # V peers processed and ready to save
        self.pipeline_processed = {}  # {node_id: data}

        self.stop_thread = False
        self.node = node
        self.server = self.node.server
        self.timeout = time.time() + timeout
        self.limit = limit

    def stop(self):
        self.stop_thread = True

    def _handle_peers_message(self, node, message):
        received = time.time()
        message = read_peers(node.server.btctxstore, message)
        if message is None:
            return  # dont care about this message
        with self.pipeline_mutex:
            data = self.pipeline_scanning.get(message.sender)
            if data is None:
                return  # not being scanned
            _log.info("Received peers from {0}!".format(
                node_id_to_address(message.sender))
            )
            data["latency"]["peers"] = received - data["latency"]["peers"]
            data["peers"] = storjnode.util.chunks(message.body, 20)

            # add previously unknown peers
            for peer in data["peers"]:
                scanning = peer in self.pipeline_scanning
                scanned = peer in self.pipeline_scanned
                processed = peer in self.pipeline_processed
                testing_bandwidth = (
                    self.pipeline_bandwidth_test is not None and
                    peer == self.pipeline_bandwidth_test[0]
                )
                if not (scanning or scanned or processed or testing_bandwidth):
                    self.pipeline_scanning[peer] = copy.deepcopy(DEFAULT_DATA)

            self._check_scan_complete(message.sender, data)

    def _handle_info_message(self, node, message):
        received = time.time()
        message = read_info(node.server.btctxstore, message)
        if message is None:
            return  # dont care about this message
        with self.pipeline_mutex:
            data = self.pipeline_scanning.get(message.sender)
            if data is None:
                return  # not being scanned
            _log.info("Received info from {0}!".format(
                node_id_to_address(message.sender))
            )
            data["latency"]["info"] = received - data["latency"]["info"]
            data["version"] = {
                "protocol": message.version,
                "storjnode": message.body.version
            }
            data["storage"] = message.body.storage._asdict()
            data["network"] = message.body.network._asdict()
            data["platform"] = message.body.platform._asdict()
            data["btcaddress"] = storjnode.util.node_id_to_address(
                message.body.btcaddress
            )
            self._check_scan_complete(message.sender, data)

    def _check_scan_complete(self, nodeid, data):
        # expect caller to have pipeline mutex

        if data["peers"] is None:
            return  # peers not yet received
        if data["network"] is None:
            return  # info not yet received

        # move to scanned
        print("Moving {0} to scanned".format(
            storjnode.util.node_id_to_address(nodeid))
        )
        del self.pipeline_scanning[nodeid]
        self.pipeline_scanned[nodeid] = data

        txt = ("Scan complete for {0}, "
               "scanned:{1}, scanning:{2}, processed:{3}!")
        _log.info(txt.format(
            node_id_to_address(nodeid),
            len(self.pipeline_scanned),
            len(self.pipeline_scanning),
            len(self.pipeline_processed),
        ))

    def _process_scanning(self, nodeid, data):

        # request with exponential backoff
        now = time.time()
        window = storjnode.network.WALK_TIMEOUT ** data["request"]["tries"]
        if time.time() < data["request"]["last"] + window:
            return  # wait for response

        _log.info("Requesting info/peers for {0}, try {1}!".format(
            node_id_to_address(nodeid),
            data["request"]["tries"]
        ))

        # request peers
        if data["peers"] is None:
            request_peers(self.node, nodeid)
            if data["latency"]["peers"] is None:
                data["latency"]["peers"] = now

        # request info
        if data["network"] is None:
            request_info(self.node, nodeid)
            if data["latency"]["info"] is None:
                data["latency"]["info"] = now

        data["request"]["last"] = now
        data["request"]["tries"] = data["request"]["tries"] + 1

    def _handle_bandwidth_test_error(self, results):
        with self.pipeline_mutex:

            # move to the back to scanned fifo and try again later
            nodeid, data = self.pipeline_bandwidth_test
            self.pipeline_scanned[nodeid] = data

            # free up bandwidth test for next peer
            self.pipeline_bandwidth_test = None

    def _handle_bandwidth_test_success(self, results):
        with self.pipeline_mutex:
            assert(results[0])
            nodeid, data = self.pipeline_bandwidth_test

            # save test results
            data["bandwidth"] = {
                "send": results[1]["upload"],
                "receive": results[1]["download"]
            }

            # move peer to processed
            self.pipeline_processed[nodeid] = data
            txt = "Processed:{0}, scanned:{1}, scanning:{2}, processed:{3}!"
            _log.info(txt.format(
                node_id_to_address(nodeid),
                len(self.pipeline_scanned),
                len(self.pipeline_scanning),
                len(self.pipeline_processed),
            ))

            # free up bandwidth test for next peer
            self.pipeline_bandwidth_test = None

    def _process_bandwidth_test(self):
        # expects caller to have pipeline mutex
        not_testing_bandwidth = self.pipeline_bandwidth_test is None
        if (not_testing_bandwidth and len(self.pipeline_scanned) > 0):

            # pop first entry
            nodeid = self.pipeline_scanned.keys()[0]
            data = self.pipeline_scanned[nodeid]
            del self.pipeline_scanned[nodeid]

            # skip bandwith test
            if SKIP_BANDWIDTH_TEST:
                self.pipeline_processed[nodeid] = data
                return

            _log.info("Starting bandwidth test for: {0}".format(
                node_id_to_address(nodeid))
            )

            # start bandwidth test (timeout after 5min)
            self.pipeline_bandwidth_test = (nodeid, data)
            on_success = self._handle_bandwidth_test_success
            on_error = self._handle_bandwidth_test_error
            deferred = self.node.test_bandwidth(nodeid)
            deferred.addCallback(on_success).addErrback(on_error)

    def _process_pipeline(self):
        while not self.stop_thread and time.time() < self.timeout:
            time.sleep(THREAD_SLEEP)

            with self.pipeline_mutex:

                # exit condition pipeline empty
                if (len(self.pipeline_scanning) == 0 and
                        len(self.pipeline_scanned) == 0 and
                        self.pipeline_bandwidth_test is None):
                    return

                # exit condition enough peers processed
                if (self.limit > 0 and
                        len(self.pipeline_processed) >= self.limit):
                    return

                # send info and peer requests to found peers
                for nodeid, data in self.pipeline_scanning.copy().items():
                    self._process_scanning(nodeid, data)

                self._process_bandwidth_test()

        # done! because of timeout or stop flag

    def crawl(self):

        # add info and peers message handlers
        self.node.add_message_handler(self._handle_info_message)
        self.node.add_message_handler(self._handle_peers_message)

        # skip self
        self.pipeline_processed[self.node.get_id()] = None

        # add initial peers
        for peer in self.node.get_neighbours():
            self.pipeline_scanning[peer.id] = copy.deepcopy(DEFAULT_DATA)

        # process pipeline until done
        self._process_pipeline()

        # remove info and peers message handlers
        self.node.remove_message_handler(self._handle_info_message)
        self.node.remove_message_handler(self._handle_peers_message)

        # remove self from results
        del self.pipeline_processed[self.node.get_id()]

        return self.pipeline_processed


def crawl(node, limit=20, timeout=600):
    """Crawl the net and gather info of the nearest peers.

    The crawler will scan nodes from nearest to farthest.

    Args:
        node: Node used to crawl the network.
        limit: Number of results after which to stop, 0 to crawl entire net.
        timeout: Time in seconds after which to stop.
    """
    return Crawler(node, limit=limit, timeout=timeout).crawl()


def predictable_key(node, num):
    return "monitor_dataset_{0}_{1}".format(node.get_address(), str(num))


def find_next_free_dataset_num(node):

    # probe for free slots with exponentially increasing steps
    upper_bound, exponant = 0, 0
    while node[predictable_key(node, upper_bound)] is not None:
        upper_bound = 2 ** exponant
        exponant += 1

    # wrapper to find used slots
    class CompareObject(object):
        def __gt__(bisect_self, index):
            return node[predictable_key(node, index)] is not None

    # A read only list where the index is the value [0,1,2,3,4 ...]
    class ListObject(object):
        def __getitem__(self, index):
            return index

        def __len__(self):
            return upper_bound + 1

    # binary search to find fist free slot
    return bisect.bisect_left(ListObject(), CompareObject())


def create_shard(node, num, begin, end, processed):

    # encode processed data
    encoded_processed = {}
    if processed:
        for nodeid, data in processed.items():
            node_address = node_id_to_address(nodeid)
            data["peers"] = [node_id_to_address(p) for p in data["peers"]]
            del data["request"]
            encoded_processed[node_address] = data

    # write info to shard
    shard = BytesIO()
    shard.write(json.dumps({
        "node": node.get_address(),
        "num": num,
        "begin": begin,
        "end": end,
        "processed": encoded_processed,
    }, indent=2))
    return shard


class Monitor(object):

    def __init__(self, node, config, limit=20,
                 interval=3600, on_crawl_complete=None):
        self.on_crawl_complete = on_crawl_complete
        self.config = config
        self.node = node
        self.limit = limit + 1  # + 1 because of initial node
        self.interval = interval
        self.mutex = RLock()
        self.crawler = None
        self.last_crawl = 0
        self.stop_thread = False
        self.dataset_num = find_next_free_dataset_num(self.node)
        self.thread = Thread(target=self.monitor)
        self.thread.start()

    def stop(self):
        _log.info("Stopping monitor!")
        with self.mutex:
            if self.crawler is not None:
                self.crawler.stop()
        self.stop_thread = True
        self.thread.join()

    def monitor(self):
        _log.debug("started monitor thread")
        while not self.stop_thread:
            time.sleep(THREAD_SLEEP)

            if not ((self.last_crawl + self.interval) < time.time()):
                continue

            # crawl network
            _log.info("Crawling dataset {0}".format(self.dataset_num))
            begin = time.time()
            with self.mutex:
                self.crawler = Crawler(
                    self.node, limit=self.limit, timeout=self.interval
                )
            processed = self.crawler.crawl()

            end = time.time()

            # create shard
            shard = create_shard(self.node, self.dataset_num,
                                 begin, end, processed)

            # save results to store
            shardid = storjnode.storage.shard.get_id(shard)
            storjnode.storage.manager.add(self.config["storage"], shard)
            _log.info("Saved dataset {0} as shard {1}".format(
                self.dataset_num, shardid
            ))

            # add store predictable id to dht
            key = predictable_key(self.node, self.dataset_num)
            self.node[key] = shardid
            _log.info("Added DHT entry {0} => {1}".format(key, shardid))

            # call handler if given
            if self.on_crawl_complete is not None:
                self.on_crawl_complete(key, shard)

            # update dataset num and last crawl time
            self.dataset_num = self.dataset_num + 1
            self.last_crawl = time.time()
