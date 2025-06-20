import os
import random
import subprocess
import signal
import json
import time
from threading import Thread
from typing import Dict
from uuid import uuid4

import requests
from eth_account.hdaccount import generate_mnemonic
from flask import Flask, Response, request
from flask_cors import CORS, cross_origin
from web3 import Web3

from eth_sandbox import *

app = Flask(__name__)
CORS(app)

HTTP_PORT = os.getenv("HTTP_PORT", "8545")
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "https://eth-pokt.nodies.app") # chosen from https://chainlist.org/chain/1
RPC_KILL_TIMEOUT = int(os.getenv("RPC_KILL_TIMEOUT", 10 * 60))

# ANSI escape sequences for text color
INFO_COLOR = '\033[94m'  # Blue
WARNING_COLOR = '\033[93m'  # Yellow
ERROR_COLOR = '\033[91m'  # Red
END_COLOR = '\033[0m'  # Reset to default color

try:
    os.mkdir("/tmp/instances-by-uuid")
    print(INFO_COLOR + "INFO: created /tmp/instances-by-uuid" + END_COLOR)
except:
    # Check if the directory exists
    if not os.path.isdir("/tmp/instances-by-uuid"):
        print(ERROR_COLOR + "ERROR: /tmp/instances-by-uuid does not exist and cannot be created" + END_COLOR)
    else:
        print(INFO_COLOR + "INFO: /tmp/instances-by-uuid already exists" + END_COLOR)


def has_instance_by_uuid(uuid: str) -> bool:
    return os.path.exists(f"/tmp/instances-by-uuid/{uuid}")


def get_instance_by_uuid(uuid: str) -> Dict:
    with open(f"/tmp/instances-by-uuid/{uuid}", 'r') as f:
        return json.loads(f.read())


def delete_instance_info(node_info: Dict):
    os.remove(f'/tmp/instances-by-uuid/{node_info["uuid"]}')


def create_instance_info(node_info: Dict):
    with open(f'/tmp/instances-by-uuid/{node_info["uuid"]}', "w+") as f:
        f.write(json.dumps(node_info))


def really_kill_node(node_info: Dict):
    print(f"killing node {node_info['uuid']}")

    delete_instance_info(node_info)

    os.kill(node_info["pid"], signal.SIGTERM)


def kill_node(node_info: Dict):
    # LA CTF: increase timeout to 1 hour as a quality-of-life fix
    time.sleep(RPC_KILL_TIMEOUT)

    if not has_instance_by_uuid(node_info["uuid"]):
        return

    really_kill_node(node_info)


def launch_node() -> Dict:
    port = random.randrange(30000, 60000)
    mnemonic = generate_mnemonic(12, "english")
    uuid = str(uuid4())

    proc = subprocess.Popen(
        args=[
            "/root/.foundry/bin/anvil",
            "--accounts",
            "2",  # first account is the deployer, second account is for the user
            "--balance",
            "5000",
            "--mnemonic",
            mnemonic,
            "--port",
            str(port),
            "--fork-url",
            ETH_RPC_URL,
            "--chain-id",
            "1337",
            "--no-rate-limit",
            "--block-base-fee-per-gas",
            "0",
            "--gas-price",
            "0",
            "--block-time",
            "10"
        ],
    )

    web3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    while True:
        if proc.poll() is not None:
            return None
        if web3.isConnected():
            break
        time.sleep(0.1)

    node_info = {
        "port": port,
        "mnemonic": mnemonic,
        "pid": proc.pid,
        "uuid": uuid,
    }

    reaper = Thread(target=kill_node, args=(node_info,))
    reaper.start()
    return node_info


def is_request_authenticated(request):
    token = request.headers.get("Authorization")

    return token == f"Bearer {get_shared_secret()}"


@app.route("/")
def index():
    return "ok"


@app.route("/new", methods=["POST"])
@cross_origin()
def create():
    if not is_request_authenticated(request):
        return {
            "ok": False,
            "error": "nice try"
        }

    print(f"launching node")

    node_info = launch_node()
    if node_info is None:
        print(f"failed to launch node")
        return {
            "ok": False,
            "error": "error_starting_chain",
            "message": "An error occurred while starting the chain",
        }
    create_instance_info(node_info)

    print(f"launched node (uuid={node_info['uuid']}, pid={node_info['pid']})")

    return {
        "ok": True,
        "uuid": node_info['uuid'],
        "mnemonic": node_info['mnemonic'],
    }


@app.route("/kill", methods=["POST"])
@cross_origin()
def kill():
    if not is_request_authenticated(request):
        return {
            "ok": False,
            "error": "nice try"
        }

    body = request.get_json()

    uuid = body["uuid"]

    if not has_instance_by_uuid(uuid):
        print(f"no instance to kill for uuid {uuid}")
        return {
            "ok": False,
            "error": "not_running",
            "message": "No instance is running!",
        }

    really_kill_node(get_instance_by_uuid(uuid))

    return {
        "ok": True,
        "message": "Instance killed",
    }

ALLOWED_METHODS = [
    "eth_blockNumber",
    "eth_call",
    "eth_chainId",
    "eth_estimateGas",
    "eth_feeHistory",
    "eth_gasPrice",
    "eth_getBalance",
    "eth_getBlockByHash",
    "eth_getBlockByNumber",
    "eth_getCode",
    "eth_getStorageAt",
    "eth_getTransactionByHash",
    "eth_getTransactionCount",
    "eth_getTransactionReceipt",
    "eth_sendRawTransaction",
    "net_version",
    "rpc_modules",
    "web3_clientVersion"
]


@app.route("/<string:uuid>", methods=["POST"])
@cross_origin()
def proxy(uuid):
    body = request.get_json()
    if not body:
        return "invalid content type, only application/json is supported"

    if "id" not in body:
        return ""

    if not has_instance_by_uuid(uuid):
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {
                "code": -32602,
                "message": "invalid uuid specified",
            },
        }

    node_info = get_instance_by_uuid(uuid)

    if "method" not in body or not isinstance(body["method"], str):
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {
                "code": -32600,
                "message": "invalid request",
            },
        }

    ok = body["method"] in ALLOWED_METHODS

    if not ok and not is_request_authenticated(request):
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {
                "code": -32600,
                "message": "invalid request",
            },
        }

    resp = requests.post(f"http://127.0.0.1:{node_info['port']}", json=body)
    response = Response(resp.content, resp.status_code,
                        resp.raw.headers.items())
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=HTTP_PORT)
