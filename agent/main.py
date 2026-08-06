import os
import sys
from pathlib import Path

# The agent resource is a remote proxy and cannot change inference options.
# Mark this process so custom actions can distinguish that case from local
# unit-test resources; the host must configure DirectML before binding it.
os.environ["MAA_AGENT_SERVER_PROCESS"] = "1"

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker

import my_action
import my_reco
import login
import select_filtered_sprites
import auto_aim_throw


def main():
    project_root = Path(__file__).resolve().parent.parent
    Tasker.set_log_dir(str(project_root / "debug"))

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)
        
    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
