# OBS - Move PTZ Camera
Uses OBS scenes to move a PTZ USB camera

## How it works
Some USB PTZ cameras can be moved using the camer'a properties in Windows 11. This tool uses python to manipulate the PTZ properties to move the camera based on the selected scene. The python file listens for scene changes and moves the camera to the predefined PTZ coordinates.

Basically:
1. You add a bunch of scenes
2. Assign PTZ coordinates to the scene using the dock
3. The python file listens for scene changes and applies the PTZ coordinates for the new scene

## Requirements
There are several requirements for this file. They are the following:
1. Python 3.12 and up
2. obsws_python and duvc_ctl
    - The script should auto install these libraries, but if not, install using the following command after python is installed
    - ```pip install obsws_python duvc_ctl```

## Instructions
Installing the tool is pretty simple. Essentially:

### Setting up the WebSocket
1. Set up your WebSocket connection by navigating to:
    - ```Tools > WebSocket Server Settings```
2. Check the "Enable WebSocket server" checkbox and grab the Server Password by selecting:
    - Show Connect Info > Server Password
    - Copy this password, it's used in the next section
3. Apply the settings and close the WebSocket windows

### Adding the lua script
1. Open the Scripts window by going to the following menu in OBS:
    - ```Tools > Scripts```
2. Click the + button and select the obs_start_python.lua file
3. Add the WebSocket password from the above steps to the "OBS WebSocket Password Override" field
4. Reload the lua script by right clicking on it and selecting "Reload"

### Adding the Dock
1. Open the Custom Browser Docks panel by going to:
    - ```Docks > Custom Browser Docks```
2. Add a new dock by adding the following information:
    - Dock Name: ```PTZ Camera Controls```
    - URL: ```127.0.0.1:8787```
3. Select "Apply" to save the dock
    - If the lua script started the python file correctly, you should see a dragable panel