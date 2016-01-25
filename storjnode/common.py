import os

# constants
PROTOCOL_VERSION = 1
THREAD_SLEEP = 0.1
MAX_PACKAGE_DATA = 548  # 576 MTU - 20 IPv4 Header - 8 UDP Header == 548

# paths
STORJ_HOME = os.path.join(os.path.expanduser("~"), ".storj")
CONFIG_PATH = os.path.join(STORJ_HOME, "cfg.json")

DEFAULT_BOOTSTRAP_NODES = [

    # storj stable
    ["104.236.1.59", 4653],  # <- legacy test group b

    # storjnode-bootstrap-uswest
    ["128.199.77.212", 5000], ["128.199.77.212", 5001],
    ["128.199.77.212", 5002], ["128.199.77.212", 5003],
    ["128.199.77.212", 5004], ["128.199.77.212", 5005],
    ["128.199.77.212", 5006], ["128.199.77.212", 5007],

    # storjnode-bootstrap-eu01
    ["46.101.238.187", 5000], ["46.101.238.187", 5001],
    ["46.101.238.187", 5002], ["46.101.238.187", 5003],
    ["46.101.238.187", 5004], ["46.101.238.187", 5005],
    ["46.101.238.187", 5006], ["46.101.238.187", 5007],

    # storjnode-bootstrap-asia01
    ["128.199.187.182", 5000], ["128.199.187.182", 5001],
    ["128.199.187.182", 5002], ["128.199.187.182", 5003],
    ["128.199.187.182", 5004], ["128.199.187.182", 5005],
    ["128.199.187.182", 5006], ["128.199.187.182", 5007],

    # storj develop (us east)
    ["159.203.64.230", 4653],  # <- merge legacy test group b
    ["159.203.64.230", 5000], ["159.203.64.230", 5001],
    ["159.203.64.230", 5002], ["159.203.64.230", 5003],
    ["159.203.64.230", 5004], ["159.203.64.230", 5005],
    ["159.203.64.230", 5006], ["159.203.64.230", 5007],

    # evilcorp
    ["188.166.69.187", 5000], ["188.166.69.187", 5001],
    ["188.166.69.187", 5002], ["188.166.69.187", 5003],
    ["188.166.69.187", 5004], ["188.166.69.187", 5005],
    ["188.166.69.187", 5006], ["188.166.69.187", 5007],

    # matthew
    ["158.69.201.105", 6770],   # Rendezvous server 1
    ["158.69.201.105", 63076],  # Rendezvous server 1
    ["162.218.239.6", 35839],   # IPXCORE:
    ["162.218.239.6", 38682],   # IPXCORE:
    ["185.86.149.128", 20560],  # Rendezvous 2
    ["185.86.149.128", 56701],  # Rendezvous 2
    ["185.61.148.22", 18825],   # dht msg 2
    ["185.61.148.22", 25029],   # dht msg 2
]
