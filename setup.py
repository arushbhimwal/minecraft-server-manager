import sys
import platform
import subprocess
import os
import requests
import logging
import tkinter as tk
from tkinter import ttk, messagebox

# -----------------------------------------------------------------------------
# Logging configuration
# -----------------------------------------------------------------------------
logging.basicConfig(
    filename='server_setup.log',      # Write logs to this file
    filemode='a',                     # Append to existing logs
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG               # Capture debug and higher-severity logs
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Utility functions for downloading, EULA, and start script generation
# -----------------------------------------------------------------------------

def download_file(url: str, dest: str):
    """
    Download a file from the given URL and save it at the dest path.

    This streams the response to avoid loading entire file in memory.
    Raises HTTPError on bad status.
    """
    logger.debug(f"Starting download: {url} -> {dest}")
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.debug(f"Finished download: {dest}")


def write_eula(workdir: str):
    """
    Create eula.txt in the working directory to accept Mojang's EULA.

    The Minecraft server requires this to run without manual intervention.
    """
    path = os.path.join(workdir, 'eula.txt')
    with open(path, 'w') as f:
        f.write('eula=true')
    logger.debug(f"EULA accepted in: {path}")


def write_start_script(workdir: str, jar: str, min_ram: str, max_ram: str):
    """
    Generate a platform-appropriate start script in the working directory.

    On Windows: creates start.bat
    On Unix (macOS/Linux): creates start.sh and marks executable
    """
    is_windows = platform.system() == 'Windows'
    script_name = 'start.bat' if is_windows else 'start.sh'
    script_path = os.path.join(workdir, script_name)

    # Build Java command with memory flags and no GUI
    java_cmd = f"java -Xms{min_ram} -Xmx{max_ram} -jar {jar} nogui"
    content = f"@echo off\n{java_cmd}" if is_windows else f"#!/bin/bash\n{java_cmd}"

    with open(script_path, 'w') as f:
        f.write(content)

    # On Unix, make script executable
    if not is_windows:
        os.chmod(script_path, 0o755)

    logger.debug(f"Start script created: {script_path}")

# -----------------------------------------------------------------------------
# Functions to fetch version data from external APIs
# -----------------------------------------------------------------------------

def fetch_manifest():
    """
    Retrieve the official Mojang version manifest.

    Returns a list of dicts, each with 'id' and 'type' (release, snapshot, alpha, beta).
    """
    url = 'https://launchermeta.mojang.com/mc/game/version_manifest.json'
    logger.debug("Fetching Minecraft version manifest...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('versions', [])


def sort_versions(ids):
    """
    Sort a list of version strings semantically in descending order.

    Handles alpha- and beta- prefixes by mapping them before numeric sort.
    """
    def key(v):
        # Normalize alpha/beta to numeric prefixes for sorting
        normalized = v.replace('alpha-', '0.0.').replace('beta-', '0.0.')
        parts = normalized.split('.')
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    return sorted(ids, key=key, reverse=True)


def fetch_paper_versions():
    """
    Query PaperMC API for supported Minecraft versions.

    Returns a list of version IDs (e.g. '1.20.4').
    """
    url = 'https://api.papermc.io/v2/projects/paper'
    logger.debug("Fetching PaperMC versions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('versions', [])


def fetch_forge_promos():
    """
    Download Forge promotions JSON containing recommended and latest builds.

    Returns a dict mapping version-promo keys to loader build numbers.
    """
    url = 'https://files.minecraftforge.net/maven/net/minecraftforge/forge/promotions_slim.json'
    logger.debug("Fetching Forge promotions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json().get('promos', {})


def fetch_forge_versions():
    """
    Extract recommended game versions from Forge promotions.

    Returns sorted list of version strings.
    """
    promos = fetch_forge_promos()
    recommended = {k.replace('-recommended', '') for k in promos if k.endswith('-recommended')}
    return sorted(recommended, reverse=True)


def fetch_forge_loader_versions(game_version: str):
    """
    Get specific Forge loader build numbers for a given game version.

    Searches promos by prefix and returns sorted list.
    """
    promos = fetch_forge_promos()
    builds = {v for k, v in promos.items() if k.startswith(f"{game_version}-")}
    return sorted(builds, reverse=True)


def fetch_fabric_versions():
    """
    Retrieve list of all Fabric-supported Minecraft game versions.
    """
    url = 'https://meta.fabricmc.net/v2/versions/game'
    logger.debug("Fetching Fabric game versions...")
    resp = requests.get(url)
    resp.raise_for_status()
    return [entry['version'] for entry in resp.json()]


def fetch_fabric_loader_versions(game_version: str):
    """
    Retrieve Fabric loader versions compatible with a specific game version.
    """
    url = f'https://meta.fabricmc.net/v2/versions/loader/{game_version}'
    logger.debug(f"Fetching Fabric loader versions for {game_version}...")
    resp = requests.get(url)
    resp.raise_for_status()
    return [loader['loader']['version'] for loader in resp.json()]


def get_latest_paper_url(mc_version: str):
    """
    Fetch the latest Paper build URL and jar filename for a given game version.
    """
    url = f'https://api.papermc.io/v2/projects/paper/versions/{mc_version}'
    logger.debug(f"Fetching Paper build info for {mc_version}...")
    resp = requests.get(url)
    resp.raise_for_status()
    builds = resp.json().get('builds', [])
    if not builds:
        raise ValueError(f"No Paper builds available for {mc_version}")
    latest = builds[-1]
    jar = f"paper-{mc_version}-{latest}.jar"
    download_url = f'https://api.papermc.io/v2/projects/paper/versions/{mc_version}/builds/{latest}/downloads/{jar}'
    return download_url, jar

# -----------------------------------------------------------------------------
# Main GUI Application Definition
# -----------------------------------------------------------------------------
class ServerSetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        logger.debug("Starting ServerSetupApp GUI")
        self.title("Minecraft Server Setup")
        self.geometry("540x700")  # Mk larger to fit comments
        self._detect_environment()
        self._create_widgets()

    def _detect_environment(self):
        """
        Detect OS and CPU architecture, log for troubleshooting.
        """
        self.current_os = platform.system()
        self.arch = platform.machine()
        logger.info(f"OS detected: {self.current_os}, Arch: {self.arch}")

    def _create_widgets(self):
        """
        Construct all GUI controls: filters, selectors, RAM inputs, and buttons.
        """
        # Vanilla filters frame
        filter_frame = tk.LabelFrame(self, text="Vanilla Version Filters")
        filter_frame.pack(fill='x', padx=10, pady=5)
        self.alpha_var = tk.BooleanVar()
        self.beta_var = tk.BooleanVar()
        self.snap_var = tk.BooleanVar()
        tk.Checkbutton(filter_frame, text='Include Alpha', variable=self.alpha_var, command=self._update_versions).pack(side='left', padx=5)
        tk.Checkbutton(filter_frame, text='Include Beta', variable=self.beta_var, command=self._update_versions).pack(side='left', padx=5)
        tk.Checkbutton(filter_frame, text='Include Snapshots', variable=self.snap_var, command=self._update_versions).pack(side='left', padx=5)

        # Server type selector
        tk.Label(self, text="Server Type:").pack(anchor='w', padx=10, pady=(10,0))
        self.server_var = tk.StringVar()
        self.server_cb = ttk.Combobox(self, textvariable=self.server_var, state='readonly',
            values=['Vanilla','Paper','Forge','Fabric'])
        self.server_cb.current(0)
        self.server_cb.pack(fill='x', padx=10)
        self.server_cb.bind('<<ComboboxSelected>>', lambda e: self._update_versions())

        # Game version selector
        tk.Label(self, text="Game Version:").pack(anchor='w', padx=10, pady=(10,0))
        self.game_var = tk.StringVar()
        self.game_cb = ttk.Combobox(self, textvariable=self.game_var, state='readonly')
        self.game_cb.pack(fill='x', padx=10)
        self.game_cb.bind('<<ComboboxSelected>>', lambda e: self._update_loader_versions())

        # Loader version selector
        tk.Label(self, text="Loader Version:").pack(anchor='w', padx=10, pady=(10,0))
        self.loader_var = tk.StringVar()
        self.loader_cb = ttk.Combobox(self, textvariable=self.loader_var, state='disabled')
        self.loader_cb.pack(fill='x', padx=10)

        # RAM allocation inputs
        ram_frame = tk.LabelFrame(self, text="RAM Allocation")
        ram_frame.pack(fill='x', padx=10, pady=10)
        tk.Label(ram_frame, text="Min RAM:").grid(row=0, column=0)
        self.min_ram_var = tk.StringVar(value="1G")
        tk.Entry(ram_frame, textvariable=self.min_ram_var, width=6).grid(row=0, column=1)
        tk.Label(ram_frame, text="Max RAM:").grid(row=0, column=2)
        self.max_ram_var = tk.StringVar(value="2G")
        tk.Entry(ram_frame, textvariable=self.max_ram_var, width=6).grid(row=0, column=3)

        # Setup button
        tk.Button(self, text='Setup Server', command=self.setup_server).pack(pady=20)

        # Populate initial version lists
        self._update_versions()

    def _update_versions(self):
        """
        Refresh the list of game versions based on server type and filters.
        """
        server_type = self.server_var.get()
        versions = []
        if server_type == 'Vanilla':
            all_entries = fetch_manifest()
            filtered = []
            for e in all_entries:
                vid, vtype = e['id'], e['type']
                # Apply user filters
                if vid.startswith('alpha-') and not self.alpha_var.get(): continue
                if vid.startswith('beta-') and not self.beta_var.get(): continue
                if vtype == 'snapshot' and not self.snap_var.get(): continue
                filtered.append(vid)
            versions = sort_versions(filtered)
        elif server_type == 'Paper':
            versions = sort_versions(fetch_paper_versions())
        elif server_type == 'Forge':
            versions = sort_versions(fetch_forge_versions())
        elif server_type == 'Fabric':
            versions = sort_versions(fetch_fabric_versions())

        self.game_cb['values'] = versions
        if versions:
            self.game_cb.current(0)
        # After changing versions, update loader dropdown
        self._update_loader_versions()

    def _update_loader_versions(self):
        """
        Refresh loader versions dropdown for Forge/Fabric based on selected game version.
        """
        server_type = self.server_var.get()
        game_version = self.game_var.get()
        loaders = []
        if server_type == 'Forge':
            loaders = fetch_forge_loader_versions(game_version)
        elif server_type == 'Fabric':
            loaders = fetch_fabric_loader_versions(game_version)

        if loaders:
            self.loader_cb['values'] = sorted(loaders, reverse=True)
            self.loader_cb.current(0)
            self.loader_cb['state'] = 'readonly'
        else:
            self.loader_cb.set('')
            self.loader_cb['state'] = 'disabled'

    def setup_server(self):
        """
        Execute the download/install workflow for the chosen server type.
        Creates the server directory, downloads files, writes EULA and start script.
        """
        st = self.server_var.get()
        gv = self.game_var.get()
        lv = self.loader_var.get()
        workdir = f"minecraft_{st.lower()}_{gv}"
        os.makedirs(workdir, exist_ok=True)  # Ensure directory exists
        try:
            # Handle each server type
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
                # Run the Forge installer in server mode
                subprocess.run([sys.executable if st else "java", "-jar", inst, "--installServer"], cwd=workdir, check=True)
                # Find the resulting universal jar
                jar = next(f for f in os.listdir(workdir) if f.endswith('universal.jar'))

            elif st == 'Fabric':
                inst = f"fabric-server-launch-{lv}.jar"
                url = f"https://meta.fabricmc.net/v2/versions/loader/{gv}/{lv}/server/{inst}"
                download_file(url, os.path.join(workdir, inst))
                subprocess.run([sys.executable if st else "java", "-jar", inst, "server"], cwd=workdir, check=True)
                jar = inst

            # Write EULA and start script with RAM settings
            write_eula(workdir)
            write_start_script(workdir, jar, self.min_ram_var.get(), self.max_ram_var.get())

            messagebox.showinfo("Success", f"{st} server setup complete in {workdir}")
            logger.info(f"Server setup complete at {workdir}")

        except Exception as e:
            logger.error(f"Setup failed for {st}: {e}")
            messagebox.showerror("Error", str(e))

# Application entry point
if __name__ == '__main__':
    app = ServerSetupApp()
    app.mainloop()
