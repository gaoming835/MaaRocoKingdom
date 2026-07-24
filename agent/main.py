import sys
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker

import my_action
import my_reco
import login
import select_filtered_sprites


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
