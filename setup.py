import sys
import platform
import subprocess
import os
import requests
import logging
import tkinter as tk
from tkinter import ttk, messagebox

# Configure logging to write to a log file
logging.basicConfig(
    filename='server_setup.log',  # Log file path
    filemode='a',                # Append mode
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG          # Log debug and higher severity messages
)
logger = logging.getLogger(__name__)

# Utility function: download a file from a URL to a destination path
def download_file(url: str, dest: str):
    """Download a file and save it to dest."""
    logger.debug(f"Starting download from {url} to {dest}")
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.debug(f"Downloaded file to {dest}")

# Write the EULA file to automatically accept Mojang's terms
def write_eula(workdir: str):
    """Create eula.txt with acceptance."""
    path = os.path.join(workdir, 'eula.txt')
    with open(path, 'w') as f:
        f.write('eula=true')
    logger.debug(f"Wrote eula.txt in {workdir}")

# Write a start script (Windows .bat or Unix .sh) to launch the server
def write_start_script(workdir: str, jar: str):
    """Generate platform-specific start script."""
    if platform.system() == 'Windows':
        script = os.path.join(workdir, 'start.bat')
        content = f"@echo off\njava -Xmx2G -jar {jar} nogui"
    else:
        script = os.path.join(workdir, 'start.sh')
        content = f"#!/bin/bash\njava -Xmx2G -jar {jar} nogui"
    with open(script, 'w') as f:
        f.write(content)
    if platform.system() != 'Windows':
        os.chmod(script, 0o755)
    logger.debug(f"Created start script: {script}")

# Fetch all Minecraft versions from Mojang's version manifest
def fetch_manifest():
    """Return list of version entries with 'id' and 'type'."""
    url = 'https://launchermeta.mojang.com/mc/game/version_manifest.json'
    logger.debug("Fetching Minecraft version manifest...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('versions', [])

# Sort version strings in descending semantic order
def sort_versions(ids):
    """Sort version identifiers semantically, newest first."""
    def key(v):
        parts = v.replace('alpha-', '0.0.').replace('beta-', '0.0.').split('.')
        return tuple(int(p) if p.isdigit() else 0 for p in parts)
    return sorted(ids, key=key, reverse=True)

# Fetch PaperMC versions
def fetch_paper_versions():
    url = 'https://api.papermc.io/v2/projects/paper'
    logger.debug("Fetching PaperMC versions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('versions', [])

# Fetch Forge promotions JSON
def fetch_forge_promos():
    url = 'https://files.minecraftforge.net/maven/net/minecraftforge/forge/promotions_slim.json'
    logger.debug("Fetching Forge promotions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('promos', {})

# Fetch recommended Forge game versions
def fetch_forge_versions():
    promos = fetch_forge_promos()
    versions = {key.replace('-recommended', '') for key in promos if key.endswith('-recommended')}
    return sorted(versions, reverse=True)

# Fetch Forge loader versions for a game version
def fetch_forge_loader_versions(game_version: str):
    promos = fetch_forge_promos()
    loaders = set()
    for key, val in promos.items():
        if key.startswith(f"{game_version}-"):
            loaders.add(val)
    return sorted(loaders, reverse=True)

# Fetch Fabric game versions
def fetch_fabric_versions():
    url = 'https://meta.fabricmc.net/v2/versions/game'
    logger.debug("Fetching Fabric game versions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return [entry.get('version') for entry in resp.json()]

# Fetch Fabric loader versions for a game version
def fetch_fabric_loader_versions(game_version: str):
    url = 'https://meta.fabricmc.net/v2/versions/loader'
    logger.debug("Fetching Fabric loader versions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return [e['version'] for e in resp.json() if e.get('gameVersion') == game_version]

# Fetch Velocity proxy builds for a game version
def fetch_proxy_builds(game_version: str):
    url = f'https://api.papermc.io/v2/projects/velocity/versions/{game_version}'
    logger.debug(f"Fetching Velocity builds for version {game_version}...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('builds', [])

# Get latest Paper build URL and jar
def get_latest_paper_url(mc_version: str):
    url = f'https://api.papermc.io/v2/projects/paper/versions/{mc_version}'
    logger.debug(f"Fetching latest Paper build for version {mc_version}...")
    resp = requests.get(url)
    resp.raise_for_status()
    builds = resp.json().get('builds', [])
    if not builds:
        raise ValueError(f"No Paper builds found for version {mc_version}")
    latest = builds[-1]
    jar = f"paper-{mc_version}-{latest}.jar"
    download_url = f'https://api.papermc.io/v2/projects/paper/versions/{mc_version}/builds/{latest}/downloads/{jar}'
    return download_url, jar

# Main GUI application class
class ServerSetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        logger.debug("Initializing GUI application...")
        self.title("Minecraft Server Setup")
        self.geometry("520x600")
        self._detect_environment()
        self._create_widgets()

    def _detect_environment(self):
        """Detect and log OS and architecture."""
        self.os = platform.system()
        self.arch = platform.machine()
        logger.debug(f"Detected OS={self.os}, ARCH={self.arch}")

    def _create_widgets(self):
        """Create and layout GUI components."""
        # Checkbuttons for Vanilla version filters
        self.alpha_var = tk.BooleanVar(value=False)
        self.beta_var = tk.BooleanVar(value=False)
        self.snap_var = tk.BooleanVar(value=False)
        cb_frame = tk.Frame(self)
        cb_frame.pack(pady=10)
        tk.Checkbutton(cb_frame, text='Include Alpha', variable=self.alpha_var, command=self._update_versions).pack(side='left')
        tk.Checkbutton(cb_frame, text='Include Beta', variable=self.beta_var, command=self._update_versions).pack(side='left')
        tk.Checkbutton(cb_frame, text='Include Snapshots', variable=self.snap_var, command=self._update_versions).pack(side='left')

        # Server type selector
        tk.Label(self, text="Server Type:").pack(pady=(20,0))
        self.server_var = tk.StringVar()
        self.server_cb = ttk.Combobox(self, textvariable=self.server_var, state='readonly',
                                      values=['Vanilla','Paper','Forge','Fabric','Proxy'])
        self.server_cb.current(0)
        self.server_cb.pack()
        self.server_cb.bind('<<ComboboxSelected>>', lambda e: self._update_versions())

        # Game version selector
        tk.Label(self, text="Game Version:").pack(pady=(20,0))
        self.game_var = tk.StringVar()
        self.game_cb = ttk.Combobox(self, textvariable=self.game_var, state='readonly', width=50)
        self.game_cb.pack()
        self.game_cb.bind('<<CombomboxSelected>>', lambda e: self._update_loader_versions())

        # Loader version selector
        tk.Label(self, text="Loader Version:").pack(pady=(20,0))
        self.loader_var = tk.StringVar()
        self.loader_cb = ttk.Combobox(self, textvariable=self.loader_var, state='disabled', width=50)
        self.loader_cb.pack()

        # Setup button
        tk.Button(self, text='Setup Server', command=self.setup_server).pack(pady=30)

        # Initial population
        self._update_versions()

    def _update_versions(self):
        """Update game versions list based on server type and filters."""
        st = self.server_var.get()
        games = []
        if st == 'Vanilla':
            manifest = fetch_manifest()
            ids = []
            for entry in manifest:
                vid, vtype = entry['id'], entry['type']
                if vid.startswith('alpha-') and not self.alpha_var.get(): continue
                if vid.startswith('beta-') and not self.beta_var.get(): continue
                if vtype == 'snapshot' and not self.snap_var.get(): continue
                ids.append(vid)
            games = sort_versions(ids)
        elif st == 'Paper':
            games = sort_versions(fetch_paper_versions())
        elif st == 'Forge':
            games = sort_versions(fetch_forge_versions())
        elif st == 'Fabric':
            games = sort_versions(fetch_fabric_versions())
        elif st == 'Proxy':
            games = sort_versions(fetch_papermc_versions())

        self.game_cb['values'] = games
        if games:
            self.game_cb.current(0)
        self._update_loader_versions()

    def _update_loader_versions(self):
        """Update loader versions list based on selected game version."""
        st = self.server_var.get()
        gv = self.game_var.get()
        loaders = []
        if st == 'Forge':
            loaders = fetch_forge_loader_versions(gv)
        elif st == 'Fabric':
            loaders = fetch_fabric_loader_versions(gv)
        elif st == 'Proxy':
            loaders = fetch_proxy_builds(gv)

        if loaders:
            self.loader_cb['values'] = sorted(loaders, reverse=True)
            self.loader_cb.current(0)
            self.loader_cb['state'] = 'readonly'
        else:
            self.loader_cb.set('')
            self.loader_cb['state'] = 'disabled'

    def setup_server(self):
        """Perform the download and setup of the chosen server."""
        st = self.server_var.get()
        gv = self.game_var.get()
        lv = self.loader_var.get()
        workdir = f"minecraft_{st.lower()}_{gv}"
        os.makedirs(workdir, exist_ok=True)
        try:
            if st == 'Vanilla':
                url, jar = 'https://launcher.mojang.com/v1/objects/server.jar', 'server.jar'
                download_file(url, os.path.join(workdir, jar))
            elif st == 'Paper':
                url, jar = get_latest_paper_url(gv)
                download_file(url, os.path.join(workdir, jar))
            elif st == 'Forge':
                inst = f"forge-{gv}-{lv}-installer.jar"
                url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{gv}-{lv}/{inst}"
                download_file(url, os.path.join(workdir, inst))
                subprocess.run(["java","-jar",inst,"--installServer"], cwd=workdir, check=True)
                jar = next(f for f in os.listdir(workdir) if f.endswith('universal.jar'))
            elif st == 'Fabric':
                inst = f"fabric-server-launch-{lv}.jar"
                url = f"https://meta.fabricmc.net/v2/versions/loader/{gv}/{lv}/server/{inst}"
                download_file(url, os.path.join(workdir, inst))
                subprocess.run(["java","-jar",inst,"server"], cwd=workdir, check=True)
                jar = inst
            else:  # Proxy
                jar = f"velocity-{lv}.jar"
                url = f"https://api.papermc.io/v2/projects/velocity/versions/{gv}/builds/{lv}/downloads/{jar}"
                download_file(url, os.path.join(workdir, jar))

            write_eula(workdir)
            write_start_script(workdir, jar)
            messagebox.showinfo("Success", f"{st} setup complete in {workdir}")
            logger.info(f"Server setup complete: {workdir}")
        except Exception as e:
            logger.error(f"Setup failed: {e}")
            messagebox.showerror("Error", str(e))

# Entry point for the application
if __name__ == '__main__':
    app = ServerSetupApp()
    app.mainloop()
