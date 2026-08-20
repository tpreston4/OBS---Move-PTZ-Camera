local obs = obslua


local python_executable = "pythonw"   -- "pythonw" avoids a console window popping up
local python_script_path = ""         -- filled in by script_defaults(), relative to this .lua file's folder
local preset_file_path = ""           -- filled in by script_defaults()
local dock_port = 8787
local auto_enable_websocket = true
local obs_ws_port_override = 0        -- 0 = auto-detect from OBS's own config
local obs_ws_password_override = ""   -- blank = auto-detect from OBS's own config

-- =========================================================================
-- HELPERS
-- =========================================================================

local function is_windows()
    return package.config:sub(1, 1) == '\\'
end

local function get_pid_file_path()
    local dir = preset_file_path:match("(.*[\\/])") or ""
    return dir .. "obs_camera_control.pid"
end

local function shell_escape(path)
    return '"' .. path .. '"'
end

local function get_obs_config_root()
    if is_windows() then
        return (os.getenv("APPDATA") or "") .. "\\obs-studio\\"
    else
        local home = os.getenv("HOME") or ""
        -- macOS
        local mac_path = home .. "/Library/Application Support/obs-studio/"
        local f = io.open(mac_path .. "global.ini", "r")
        if f then f:close(); return mac_path end
        -- Linux
        return (os.getenv("XDG_CONFIG_HOME") or (home .. "/.config")) .. "/obs-studio/"
    end
end

local function read_ini_section(path, section_name)
    local f = io.open(path, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()

    local section_start = content:find("%[" .. section_name .. "%]")
    if not section_start then return nil end

    local body_start = content:find("\n", section_start)
    if not body_start then return {} end
    body_start = body_start + 1

    local next_section = content:find("\n%[", body_start)
    local body = next_section and content:sub(body_start, next_section) or content:sub(body_start)

    local values = {}
    for line in body:gmatch("[^\r\n]+") do
        local key, value = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
        if key then
            values[key] = value
        end
    end
    return values
end

local function read_websocket_settings()
    local ini_path = get_obs_config_root() .. "global.ini"
    local section = read_ini_section(ini_path, "OBSWebSocket")
    if not section then
        return { found = false, path = ini_path }
    end
    return {
        found = true,
        path = ini_path,
        enabled = (section.ServerEnabled == "true"),
        port = tonumber(section.ServerPort),
        password = section.ServerPassword,
    }
end

local function enable_websocket_server(ini_path)
    local f = io.open(ini_path, "r")
    if not f then
        return false, "could not open " .. ini_path
    end
    local content = f:read("*a")
    f:close()

    -- Always back up before touching OBS's own config file.
    local backup = io.open(ini_path .. ".ptzbackup", "w")
    if backup then
        backup:write(content)
        backup:close()
    end

    local updated, count = content:gsub("(%[OBSWebSocket%][^%[]-)ServerEnabled%s*=%s*%a+", "%1ServerEnabled=true")
    if count == 0 then
        -- No existing ServerEnabled line in that section; add one right after the header.
        updated, count = content:gsub("(%[OBSWebSocket%])", "%1\nServerEnabled=true", 1)
    end
    if count == 0 then
        return false, "could not find an [OBSWebSocket] section to edit"
    end

    local out = io.open(ini_path, "w")
    if not out then
        return false, "could not write to " .. ini_path .. " (check file permissions)"
    end
    out:write(updated)
    out:close()
    return true
end

local function stop_python_process()
    local pid_file = get_pid_file_path()
    local f = io.open(pid_file, "r")
    if not f then
        print("[Python Launcher] No PID file found; nothing to stop.")
        return
    end

    local pid = f:read("*l")
    f:close()

    if not pid or pid:match("^%s*$") then
        print("[Python Launcher] PID file was empty.")
        os.remove(pid_file)
        return
    end

    print("[Python Launcher] Stopping Python process with PID " .. pid .. "...")
    if is_windows() then
        os.execute(string.format('taskkill /PID %s /T /F >nul 2>&1', pid))
    else
        os.execute(string.format('kill -9 %s 2>/dev/null', pid))
    end

    os.remove(pid_file)
end

local function start_python_process(ws_port, ws_password)
    if python_script_path == "" then
        print("[Python Launcher] No script path configured, skipping start.")
        return
    end

    local command
    if is_windows() then
        command = string.format('start /b %s %s --preset-file %s --port %d --obs-port %d --obs-password %s',
            python_executable, shell_escape(python_script_path), shell_escape(preset_file_path), dock_port,
            ws_port, shell_escape(ws_password))
    else
        command = string.format('%s %s --preset-file %s --port %d --obs-port %d --obs-password %s &',
            python_executable, shell_escape(python_script_path), shell_escape(preset_file_path), dock_port,
            ws_port, shell_escape(ws_password))
    end

    print("[Python Launcher] Starting Python script in the background...")
    os.execute(command)
    print(string.format("[Python Launcher] Control panel will be at http://127.0.0.1:%d/ once it starts.", dock_port))
end

-- =========================================================================
-- OBS SCRIPT LIFECYCLE
-- =========================================================================

function script_description()
    return "Starts the PTZ camera control Python script when OBS opens and stops it cleanly when OBS closes.\n\n" ..
        "It also reads OBS's own WebSocket port/password from global.ini automatically, so nothing needs to " ..
        "be typed in or hardcoded. If the WebSocket server is disabled, it will enable it in that file and " ..
        "let you know here -- OBS needs one restart for that specific change to take effect.\n\n" ..
        "One-time setup for the control panel: once OBS is running with this script loaded, go to " ..
        "Docks -> Custom Browser Docks..., give it a name, and set the URL to " ..
        "http://127.0.0.1:<port>/ (default port 8787, see below). You can then dock that panel " ..
        "anywhere in the OBS window -- that's where you pick the camera and scene and set PTZ presets."
end

function script_defaults(settings)
    obs.obs_data_set_default_string(settings, "python_executable", "pythonw")

    obs.obs_data_set_default_string(settings, "python_script_path",
        script_path() .. "..\\..\\Supporting Python Files\\Camera Control\\V1\\obs_camera_control.py")

    local home = os.getenv("USERPROFILE") or os.getenv("HOME") or ""
    local sep = is_windows() and "\\" or "/"
    obs.obs_data_set_default_string(settings, "preset_file_path", home .. sep .. "Documents" .. sep .. "obs_stage_presets.json")

    obs.obs_data_set_default_int(settings, "dock_port", 8787)
    obs.obs_data_set_default_bool(settings, "auto_enable_websocket", true)
    obs.obs_data_set_default_int(settings, "obs_ws_port_override", 0)
    obs.obs_data_set_default_string(settings, "obs_ws_password_override", "")
end

function script_properties()
    local props = obs.obs_properties_create()

    obs.obs_properties_add_text(props, "python_executable", "Python Executable", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_path(props, "python_script_path", "Python Script Path", obs.OBS_PATH_FILE, "Python Files (*.py)", nil)
    obs.obs_properties_add_path(props, "preset_file_path", "Presets JSON File", obs.OBS_PATH_FILE_SAVE, "JSON Files (*.json)", nil)
    obs.obs_properties_add_int(props, "dock_port", "Control Panel Port", 1024, 65535, 1)

    obs.obs_properties_add_bool(props, "auto_enable_websocket",
        "Automatically enable OBS WebSocket server if it's off (edits global.ini, needs one restart to apply)")
    obs.obs_properties_add_int(props, "obs_ws_port_override", "OBS WebSocket Port Override (0 = auto-detect)", 0, 65535, 1)
    obs.obs_properties_add_text(props, "obs_ws_password_override", "OBS WebSocket Password Override (blank = auto-detect)", obs.OBS_TEXT_PASSWORD)

    return props
end

function script_update(settings)
    python_executable = obs.obs_data_get_string(settings, "python_executable")
    python_script_path = obs.obs_data_get_string(settings, "python_script_path")
    preset_file_path = obs.obs_data_get_string(settings, "preset_file_path")
    dock_port = obs.obs_data_get_int(settings, "dock_port")
    auto_enable_websocket = obs.obs_data_get_bool(settings, "auto_enable_websocket")
    obs_ws_port_override = obs.obs_data_get_int(settings, "obs_ws_port_override")
    obs_ws_password_override = obs.obs_data_get_string(settings, "obs_ws_password_override")
end

function script_load(settings)
    script_update(settings)

    stop_python_process()

    local ws = read_websocket_settings()

    if not ws.found then
        print("[WebSocket Setup] Could not locate OBS's global.ini to check the WebSocket server.")
        print("[WebSocket Setup] Make sure it's enabled via Tools -> WebSocket Server Settings,")
        print("[WebSocket Setup] or set the port/password overrides in this script's settings.")
    elseif not ws.enabled then
        print("[WebSocket Setup] OBS WebSocket server is currently disabled.")
        if auto_enable_websocket then
            local ok, err = enable_websocket_server(ws.path)
            if ok then
                print("[WebSocket Setup] Enabled it in " .. ws.path .. " (a backup of the original was saved alongside it).")
                print("[WebSocket Setup] IMPORTANT: restart OBS once for this change to take effect.")
                print("[WebSocket Setup] After that restart, this all happens automatically -- no further action needed.")
            else
                print("[WebSocket Setup] Could not enable it automatically: " .. tostring(err))
                print("[WebSocket Setup] Enable it manually via Tools -> WebSocket Server Settings instead.")
            end
        else
            print("[WebSocket Setup] Auto-enable is turned off in this script's settings.")
            print("[WebSocket Setup] Enable it manually via Tools -> WebSocket Server Settings.")
        end
    end

    local resolved_port = (obs_ws_port_override > 0) and obs_ws_port_override or (ws.port or 4455)
    local resolved_password = (obs_ws_password_override ~= "") and obs_ws_password_override or (ws.password or "")

    if resolved_password == "" then
        print("[WebSocket Setup] No password found or configured. This only works if OBS WebSocket " ..
              "authentication is disabled -- otherwise, set a password override in this script's settings.")
    end

    start_python_process(resolved_port, resolved_password)
end

function script_unload()
    stop_python_process()
end
