import os
import json
import time
import obsws_python as obsws
import duvc_ctl as duvc

# --- CONFIGURATION ---
OBS_PASSWORD = "hMd6lL4zXZIdNsio"  # Update with your OBS password
OBS_PORT = 4455
CAMERA_NAME = "HD Camera"  # Update with your exact camera name
PRESET_FILE = os.path.join(os.path.expanduser("~"), "Documents", "obs_stage_presets.json")

# Default presets structure used to create or heal the file
DEFAULT_PRESETS = {
    "stage_center": {"p": -96, "t": -2, "z": 5194},
    "stage_left": {"p": -105, "t": -3, "z": 4124},
    "stage_right": {"p": -87, "t": -5, "z": 4315},
    "stage_off_left": {"p": -100, "t": -3, "z": 4160}
}

def write_default_presets():
    """Writes the central stage configuration to the JSON file safely."""
    os.makedirs(os.path.dirname(PRESET_FILE), exist_ok=True)
    with open(PRESET_FILE, "w") as f:
        json.dump(DEFAULT_PRESETS, f, indent=4)
    print(f"[System] Resetting/Initializing stage presets file at: {PRESET_FILE}")


# Initial sanity check on script startup
if not os.path.exists(PRESET_FILE) or os.path.getsize(PRESET_FILE) == 0:
    write_default_presets()


def move_camera_to_coordinates(pan, tilt, zoom):
    """Binds to the physical Windows UVC drivers and injects PTZ movements."""
    print("Sending hardware instructions to physical optics...")
    print(f"  -> PAN : {pan} | TILT: {tilt} | ZOOM: {zoom}")
    
    try:
        cam = duvc.find_camera(CAMERA_NAME)
        if cam:
            cam.pan = pan
            cam.tilt = tilt
            cam.zoom = zoom
            cam.close()  # Safely release the camera handle
        else:
            print(f"Error: Could not find hardware device matching name '{CAMERA_NAME}'")
    except Exception as e:
        print(f"Hardware injection failed: {e}")


def on_current_program_scene_changed(data):
    """Callback function that triggers every time OBS switches scenes."""
    scene_name = data.scene_name
    
    # Dynamically extract target stages from the central configuration keys
    if scene_name in DEFAULT_PRESETS.keys():
        print(f"\n[OBS Event] Switched Scene to: '{scene_name}'")
        
        # Self-healing layer: fix file if deleted or cleared while running
        if not os.path.exists(PRESET_FILE) or os.path.getsize(PRESET_FILE) == 0:
            write_default_presets()
            
        try:
            with open(PRESET_FILE, "r") as f:
                presets = json.load(f)
        except json.JSONDecodeError:
            print("[Warning] Presets file was corrupted. Forcing a preset reset...")
            write_default_presets()
            presets = DEFAULT_PRESETS
            
        coords = presets.get(scene_name)
        if coords:
            move_camera_to_coordinates(coords["p"], coords["t"], coords["z"])


def main():
    print("Connecting to OBS WebSockets...")
    try:
        # Establish connection for immediate commands
        req_client = obsws.ReqClient(host='127.0.0.1', port=OBS_PORT, password=OBS_PASSWORD)
        
        # Verify and force Virtual Camera state active
        vcam_status = req_client.get_virtual_cam_status()
        if not vcam_status.output_active:
            print("Virtual Camera is offline. Activating it now...")
            req_client.start_virtual_cam()
            print("Virtual Camera Activated Successfully.")
        else:
            print("Virtual Camera is already active.")
            
        print("\nMonitoring Stage Scene Changes... Press CTRL+C to stop.")
        
        # Display the dynamically parsed scenes being monitored on launch
        print(f"Active Monitored Scenes: {list(DEFAULT_PRESETS.keys())}")
        
        event_client = obsws.EventClient(host='127.0.0.1', port=OBS_PORT, password=OBS_PASSWORD)
        event_client.callback.register(on_current_program_scene_changed)
        
        # Keep thread alive to listen for incoming OBS WebSocket events
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nScript terminated safely by user.")
    except Exception as e:
        print(f"\nAn error occurred (ensure OBS is running with WebSockets enabled): {e}")


if __name__ == "__main__":
    main()