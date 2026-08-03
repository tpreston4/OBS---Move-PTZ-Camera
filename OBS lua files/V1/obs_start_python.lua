local obs = obslua

-- CONFIGURATION
-- Use "pythonw" instead of "python" on Windows to run without a command window.
local python_executable = "pythonw"

-- Replace with the absolute path to your Python script
local python_script_path = "C:\\Users\\vesta\\Documents\\obs_camera_control.py"

-- Internal variable to hold the running process handle
local python_process = nil

function script_load(settings)
    local command
    
    -- Format command differently based on OS for background execution compatibility
    if package.config:sub(1,1) == '\\' then
        -- Windows: Use pythonw to prevent a command prompt window from showing up
        command = string.format('start /b %s "%s"', python_executable, python_script_path)
    else
        -- macOS / Linux
        command = string.format('%s "%s" &', python_executable, python_script_path)
    end

    print("[Python Launcher] Starting Python script in the background...")
    python_process = os.execute(command)
end

function script_unload()
    print("[Python Launcher] Stopping Python script...")
    
    if package.config:sub(1,1) == '\\' then
        -- Windows: Forcefully kill the pythonw process running the specific script name
        local filename = python_script_path:match("^.+[\\/](.+)$") or python_script_path
        os.execute(string.format('taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq %s"', filename))
        
        -- Fallback cleanup using wmic if taskkill misses the background process
        os.execute(string.format('wmic process where "caption=\'pythonw.exe\' and commandline like \'%%%s%%\'" call terminate >nul 2>&1', filename))
    else
        -- macOS / Linux: Kill process matching script name
        os.execute(string.format('pkill -f "%s"', python_script_path))
    end
end

function script_description()
    return "Automatically starts a Python script silently in the background when OBS opens and stops it when OBS closes."
end