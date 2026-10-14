# System Preparation

The following one-time setup is required per machine before running any Conda-based Snakemake workflow. Install all four components for your operating system.

=== "Windows"

    1. **Mamba/Conda:** Install **Miniforge** (recommended):
        - Download from [conda-forge.github.io/miniforge](https://conda-forge.github.io/miniforge)
        - Run the `.exe` installer and select "Add to PATH" during installation
        - Verify: Open PowerShell or CMD and run `mamba --version`

    2. **Python:** Comes bundled with Miniforge (Python 3.9+ recommended).
        - Verify: `python --version`

    3. **Snakemake:** Install the Snakemake engine in a dedicated environment:

        ```powershell
        mamba create -n snakemake -c conda-forge -c bioconda snakemake
        mamba activate snakemake
        ```

    4. **FDO Client:** Install your custom command-line client:
        - Download the latest `fdo-client-windows-x64.exe` CLI from [https://github.com/MaRDI4NFDI/mardi_doip_server]
        - Rename to `fdo-run-client.exe` and move to a directory in your PATH (e.g., `C:\Tools\`)
        - Verify: `fdo-run-client --version`

=== "Linux"

    1. **Mamba/Conda:** Install **Miniforge** (recommended):
        ```bash
        wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
        bash Miniforge3-Linux-x86_64.sh
        # Follow prompts, then reload shell: source ~/.bashrc
        ```
        - Verify: `mamba --version`

    2. **Python:** Comes bundled with Miniforge (Python 3.9+ recommended).
        - Verify: `python --version`

    3. **Snakemake:** Install the Snakemake engine in a dedicated environment:

        ```bash
        mamba create -n snakemake -c conda-forge -c bioconda snakemake
        mamba activate snakemake
        ```

    4. **FDO Client:** Install your custom command-line client:
        ```bash
        wget https://github.com/MaRDI4NFDI/mardi_doip_server/releases/latest/download/fdo-client-linux-x64 -O fdo-run-client
        chmod +x fdo-run-client
        sudo mv fdo-run-client /usr/local/bin/
        ```
        - Verify: `fdo-run-client --version`

=== "macOS"

    1. **Mamba/Conda:** Install **Miniforge** (recommended):
        - For Intel Macs: Download `Miniforge3-MacOSX-x86_64.sh`
        - For Apple Silicon (M1/M2/M3): Download `Miniforge3-MacOSX-arm64.sh`
        - Both available at [conda-forge.github.io/miniforge](https://conda-forge.github.io/miniforge)
        - Run: `bash Miniforge3-MacOSX-*.sh` and follow prompts
        - Reload shell: `source ~/.zshrc` (or `~/.bash_profile` for older macOS)
        - Verify: `mamba --version`

    2. **Python:** Comes bundled with Miniforge (Python 3.9+ recommended).
        - Verify: `python --version`

    3. **Snakemake:** Install the Snakemake engine in a dedicated environment:

        ```bash
        mamba create -n snakemake -c conda-forge -c bioconda snakemake
        mamba activate snakemake
        ```

    4. **FDO Client:** Install your custom command-line client:
        ```bash
        # For Intel Macs
        curl -L https://github.com/MaRDI4NFDI/mardi_doip_server/releases/latest/download/fdo-client-macos-x64 -o fdo-run-client

        # For Apple Silicon (M1/M2/M3)
        curl -L https://github.com/MaRDI4NFDI/mardi_doip_server/releases/latest/download/fdo-client-macos-arm64 -o fdo-run-client

        chmod +x fdo-run-client
        sudo mv fdo-run-client /usr/local/bin/
        ```
        - Verify: `fdo-run-client --version`

---

Next: [Creating a Workflow FDO](workflows_create.md)
