# blender-claude
Connect Claude to Blender

Follow these steps to configure Claude Desktop to run scripts in Blender and generate 3D models:
- Download and install latest Python version from https://www.python.org/downloads/
  - You can make sure Python is installed by running the following command in a terminal:
  - `python3 --version`
- Run the following commands in a terminal:
  - `pip3 install mcp`
  - `pip3 install httpx`
- Download `blender_mcp.py` and `blender_bridge.py` from this repository.
- Open Blender, and go "Scripting -> Open (`blender_bridge.py`) -> Run Script".
- Open Claude Desktop, go to "Settings -> Developer -> Edit Config".
  - Open file `claude_desktop_config.json` and add the content of `claude_desktop_config.json` from this repository into it.
  - Make sure to fix the path to `blender_mcp.py` in your `claude_desktop_config.json`.
- Restart claude, and instruct it to generate models in Blender.
- Have fun!
